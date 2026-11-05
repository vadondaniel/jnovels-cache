from __future__ import annotations

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
                ss.checked_at <= ?
                OR ss.source_change_pending = 1
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
    *,
    force: bool = False,
) -> list[str]:
    if force:
        with connect_db() as connection:
            return [row["series_url"] for row in connection.execute(
                "SELECT series_url FROM series_status WHERE unavailable_at IS NULL "
                "ORDER BY checked_at, series_url LIMIT ?", (limit,)
            )]
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


def mark_series_metadata_checked(
    connect_db: ConnectDb,
    series_url: str,
    checked_at: str | None = None,
) -> None:
    checked_at = checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO series_status (series_url, checked_at, metadata_checked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(series_url) DO UPDATE
            SET metadata_checked_at = excluded.metadata_checked_at,
                unavailable_at = NULL
            """,
            (series_url, checked_at, checked_at),
        )


def mark_series_unavailable(connect_db: ConnectDb, series_url: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO series_status
                (series_url, checked_at, metadata_checked_at, unavailable_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(series_url) DO UPDATE
            SET checked_at = excluded.checked_at,
                metadata_checked_at = excluded.metadata_checked_at,
                unavailable_at = excluded.unavailable_at
            """,
            (series_url, now, now, now),
        )
