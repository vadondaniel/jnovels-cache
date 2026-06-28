from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def get_cached_urls(connect_db: ConnectDb) -> set[str]:
    with connect_db() as connection:
        rows = connection.execute("SELECT url FROM books").fetchall()
    return {row["url"] for row in rows}


def record_detail_failure(
    connect_db: ConnectDb,
    url: str,
    error: Exception,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            """
            UPDATE books
            SET detail_attempts = detail_attempts + 1,
                detail_last_attempt_at = ?,
                detail_last_error = ?
            WHERE url = ?
            """,
            (now, str(error)[:500], url),
        )


def get_incomplete_detail_urls(
    connect_db: ConnectDb,
    limit: int,
    *,
    max_attempts: int | None = None,
    exclude_urls: set[str] | None = None,
) -> list[str]:
    conditions = [
        """
        (
            b.detail_cached_at IS NULL
            OR b.entry_type IS NULL
            OR b.download_url IS NULL
        )
        """
    ]
    params: list[Any] = []
    if max_attempts is not None:
        conditions.append("b.detail_attempts < ?")
        params.append(max_attempts)
    if exclude_urls:
        placeholders = ", ".join("?" for _ in exclude_urls)
        conditions.append(f"b.url NOT IN ({placeholders})")
        params.extend(sorted(exclude_urls))
    params.append(limit)

    with connect_db() as connection:
        rows = connection.execute(
            f"""
            SELECT b.url
            FROM books AS b
            WHERE {" AND ".join(conditions)}
            ORDER BY b.detail_last_attempt_at IS NOT NULL,
                     b.detail_last_attempt_at,
                     b.published_at DESC,
                     b.id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [row["url"] for row in rows]
