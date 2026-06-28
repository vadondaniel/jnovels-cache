from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def set_metadata(connect_db: ConnectDb, key: str, value: str) -> None:
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def delete_metadata(connect_db: ConnectDb, key: str) -> None:
    with connect_db() as connection:
        connection.execute("DELETE FROM metadata WHERE key = ?", (key,))


def get_search_scan_frontier(connect_db: ConnectDb) -> int:
    value = get_metadata(connect_db).get("search_scan_frontier")
    try:
        return max(int(value or 0), 0)
    except ValueError:
        return 0


def get_book_source_orders(connect_db: ConnectDb, urls: list[str]) -> dict[str, int]:
    if not urls:
        return {}
    placeholders = ", ".join("?" for _ in urls)
    with connect_db() as connection:
        rows = connection.execute(
            f"""
            SELECT url, source_order
            FROM books
            WHERE url IN ({placeholders})
              AND source_order IS NOT NULL
            """,
            urls,
        ).fetchall()
    return {row["url"]: row["source_order"] for row in rows}


def observed_source_page_shift(
    current_page: int,
    source_orders: dict[str, int],
) -> int:
    return max(
        (
            current_page - source_order // 1000
            for source_order in source_orders.values()
            if current_page > source_order // 1000
        ),
        default=0,
    )


def apply_observed_front_shift(
    frontier: int,
    applied_shift: int,
    observed_shift: int,
) -> tuple[int, int]:
    if observed_shift <= applied_shift:
        return frontier, applied_shift
    return frontier + observed_shift - applied_shift, observed_shift


def get_metadata(
    connect_db: ConnectDb,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, str]:
    def load(active_connection: sqlite3.Connection) -> dict[str, str]:
        rows = active_connection.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: row["value"] for row in rows}

    if connection is not None:
        return load(connection)
    with connect_db() as active_connection:
        return load(active_connection)


def get_public_metadata(
    connect_db: ConnectDb,
    *,
    hidden_keys: set[str],
    connection: sqlite3.Connection | None = None,
) -> dict[str, str]:
    metadata = get_metadata(connect_db, connection=connection)
    for key in hidden_keys:
        metadata.pop(key, None)
    return metadata


def prepare_legacy_reparse(connect_db: ConnectDb, legacy_reparse_key: str) -> int:
    with connect_db() as connection:
        if connection.execute(
            "SELECT 1 FROM metadata WHERE key = ?",
            (legacy_reparse_key,),
        ).fetchone():
            return 0

        before = connection.total_changes
        connection.execute(
            """
            UPDATE books
            SET entry_type = 'series',
                original_post_url = NULL
            WHERE original_post_url = url
            """
        )
        connection.execute(
            """
            UPDATE series_status
            SET metadata_checked_at = NULL
            WHERE series_url IN (
                SELECT url
                FROM books
                WHERE entry_type = 'series'
                  AND (
                      lower(title) LIKE '%webnovel%'
                      OR lower(title) LIKE '%[pdf]%'
                      OR lower(title) LIKE '%[epub]%'
                      OR lower(url) LIKE '%pdfepub%'
                  )
            )
            """
        )
        changes = connection.total_changes - before
        connection.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            (legacy_reparse_key, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    return changes


def prepare_series_metadata_refresh(
    connect_db: ConnectDb,
    parser_key: str,
    parser_version: str,
) -> int:
    with connect_db() as connection:
        current_version = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (parser_key,),
        ).fetchone()
        if current_version and current_version["value"] == parser_version:
            return 0

        before = connection.total_changes
        connection.execute(
            """
            UPDATE series_status
            SET metadata_checked_at = NULL
            WHERE unavailable_at IS NULL
            """
        )
        changes = connection.total_changes - before
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (parser_key, parser_version),
        )
    return changes


def save_sync_checkpoint(
    connect_db: ConnectDb,
    checkpoint_key: str,
    checkpoint: dict[str, Any],
) -> None:
    set_metadata(
        connect_db,
        checkpoint_key,
        json.dumps(checkpoint, ensure_ascii=False),
    )


def load_sync_checkpoint(
    connect_db: ConnectDb,
    checkpoint_key: str,
) -> dict[str, Any] | None:
    raw = get_metadata(connect_db).get(checkpoint_key)
    if not raw:
        return None
    try:
        checkpoint = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        delete_metadata(connect_db, checkpoint_key)
        return None
    return checkpoint if isinstance(checkpoint, dict) else None


def clear_sync_checkpoint(connect_db: ConnectDb, checkpoint_key: str) -> None:
    delete_metadata(connect_db, checkpoint_key)
