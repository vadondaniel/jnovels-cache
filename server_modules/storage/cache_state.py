from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def series_stale_cutoff(stale_days: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=stale_days)
    ).isoformat(timespec="seconds")


def stale_series_where_clause() -> str:
    return """
        (
            ss.unavailable_at IS NULL
            AND (
                ss.checked_at < ?
                OR EXISTS (
                    SELECT 1
                    FROM books AS b
                    WHERE b.original_post_url = ss.series_url
                      AND b.cached_at > ss.checked_at
                )
            )
        )
    """


def count_stale_series(
    connect_db: ConnectDb,
    stale_days: int,
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    def count(active_connection: sqlite3.Connection) -> int:
        return active_connection.execute(
            f"""
            SELECT COUNT(*)
            FROM series_status AS ss
            WHERE {stale_series_where_clause()}
            """,
            (series_stale_cutoff(stale_days),),
        ).fetchone()[0]

    if connection is not None:
        return count(connection)
    with connect_db() as active_connection:
        return count(active_connection)


def get_cache_health(
    connect_db: ConnectDb,
    *,
    auto_repair_max_attempts: int,
    stale_days: int,
    count_missing_series_index_entries: Callable[..., int],
    connection: sqlite3.Connection | None = None,
) -> dict[str, int]:
    def load(active_connection: sqlite3.Connection) -> dict[str, int]:
        pending = active_connection.execute(
            """
            SELECT COUNT(*)
            FROM books AS b
            WHERE b.detail_cached_at IS NULL
               OR b.entry_type IS NULL
               OR b.download_url IS NULL
            """
        ).fetchone()[0]
        paused = active_connection.execute(
            """
            SELECT COUNT(*)
            FROM books AS b
            WHERE (
                    b.detail_cached_at IS NULL
                    OR b.entry_type IS NULL
                    OR b.download_url IS NULL
                  )
              AND b.detail_attempts >= ?
            """,
            (auto_repair_max_attempts,),
        ).fetchone()[0]
        failed = active_connection.execute(
            """
            SELECT COUNT(*)
            FROM books
            WHERE detail_attempts > 0
              AND detail_last_error IS NOT NULL
            """
        ).fetchone()[0]
        stale_series = count_stale_series(
            connect_db,
            stale_days,
            connection=active_connection,
        )
        refreshable_series = active_connection.execute(
            f"""
            SELECT COUNT(*)
            FROM series_status AS ss
            WHERE ss.unavailable_at IS NULL
              AND (
                    ss.metadata_checked_at IS NULL
                    OR {stale_series_where_clause()}
                  )
            """,
            (series_stale_cutoff(stale_days),),
        ).fetchone()[0]
        return {
            "pending_details": pending,
            "failed_retries": failed,
            "paused_retries": paused,
            "stale_series": stale_series,
            "refreshable_series": refreshable_series,
            "indexed_series_total": active_connection.execute(
                "SELECT COUNT(*) FROM series_index_entries WHERE active = 1"
            ).fetchone()[0],
            "indexed_series_missing": count_missing_series_index_entries(
                connection=active_connection
            ),
        }

    if connection is not None:
        return load(connection)
    with connect_db() as active_connection:
        return load(active_connection)


def get_stale_series_urls(
    connect_db: ConnectDb,
    stale_days: int,
    limit: int,
) -> list[str]:
    with connect_db() as connection:
        rows = connection.execute(
            f"""
            SELECT ss.series_url
            FROM series_status AS ss
            WHERE {stale_series_where_clause()}
            ORDER BY ss.checked_at, ss.series_url
            LIMIT ?
            """,
            (series_stale_cutoff(stale_days), limit),
        ).fetchall()
    return [row["series_url"] for row in rows]


def get_series_metadata_backfill_urls(
    connect_db: ConnectDb,
    limit: int,
    *,
    exclude_urls: set[str] | None = None,
) -> list[str]:
    conditions = ["metadata_checked_at IS NULL", "unavailable_at IS NULL"]
    params: list[Any] = []
    if exclude_urls:
        placeholders = ", ".join("?" for _ in exclude_urls)
        conditions.append(f"series_url NOT IN ({placeholders})")
        params.extend(sorted(exclude_urls))
    params.append(limit)

    with connect_db() as connection:
        rows = connection.execute(
            f"""
            SELECT series_url
            FROM series_status
            WHERE {" AND ".join(conditions)}
            ORDER BY checked_at DESC, series_url
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [row["series_url"] for row in rows]


def get_series_refresh_urls(
    connect_db: ConnectDb,
    stale_days: int,
    limit: int,
) -> list[str]:
    urls = get_stale_series_urls(connect_db, stale_days, limit)
    remaining = limit - len(urls)
    if remaining > 0:
        urls.extend(
            get_series_metadata_backfill_urls(
                connect_db,
                remaining,
                exclude_urls=set(urls),
            )
        )
    return urls


def list_repair_failures(
    connect_db: ConnectDb,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT title, url, detail_attempts, detail_last_attempt_at, detail_last_error
            FROM books
            WHERE detail_attempts > 0
              AND detail_last_error IS NOT NULL
            ORDER BY detail_last_attempt_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def is_cached_book_url(connect_db: ConnectDb, url: str) -> bool:
    with connect_db() as connection:
        return connection.execute(
            "SELECT 1 FROM books WHERE url = ?",
            (url,),
        ).fetchone() is not None


def is_established_series_book(connect_db: ConnectDb, url: str) -> bool:
    with connect_db() as connection:
        return connection.execute(
            "SELECT 1 FROM books WHERE url = ? AND entry_type = 'series'",
            (url,),
        ).fetchone() is not None


def is_cached_series_url(connect_db: ConnectDb, url: str) -> bool:
    with connect_db() as connection:
        return connection.execute(
            """
            SELECT 1
            FROM books
            WHERE url = ?
               OR original_post_url = ?
            UNION
            SELECT 1
            FROM series_volumes
            WHERE series_url = ?
            LIMIT 1
            """,
            (url, url, url),
        ).fetchone() is not None


def set_series_preference(
    connect_db: ConnectDb,
    series_url: str,
    *,
    favorite: bool | None = None,
    hidden: bool | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        current = connection.execute(
            """
            SELECT favorite, hidden
            FROM series_preferences
            WHERE series_url = ?
            """,
            (series_url,),
        ).fetchone()
        next_favorite = int(
            favorite if favorite is not None else bool(current["favorite"]) if current else False
        )
        next_hidden = int(
            hidden if hidden is not None else bool(current["hidden"]) if current else False
        )
        connection.execute(
            """
            INSERT INTO series_preferences
                (series_url, favorite, hidden, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(series_url) DO UPDATE SET
                favorite = excluded.favorite,
                hidden = excluded.hidden,
                updated_at = excluded.updated_at
            """,
            (series_url, next_favorite, next_hidden, now),
        )
    return {
        "series_url": series_url,
        "favorite": bool(next_favorite),
        "hidden": bool(next_hidden),
    }


def cache_content_signature(connect_db: ConnectDb) -> str:
    digest = hashlib.sha256()
    with connect_db() as connection:
        book_rows = connection.execute(
            """
            SELECT title, url, image_url, published_at, entry_type,
                   original_post_url, download_url, associated_names,
                   genres, synopsis, source_order
            FROM books
            ORDER BY url
            """
        )
        for row in book_rows:
            digest.update(
                json.dumps(
                    list(row),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(b"\n")

        volume_rows = connection.execute(
            """
            SELECT series_url, label, download_url, position, release_group
            FROM series_volumes
            ORDER BY series_url, position, download_url
            """
        )
        for row in volume_rows:
            digest.update(
                json.dumps(
                    list(row),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(b"\n")
    return digest.hexdigest()
