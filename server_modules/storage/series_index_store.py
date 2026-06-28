from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone

from server_modules.storage.metadata_store import get_metadata
from server_modules.domain.models import Book
from server_modules.normalization.title_utils import series_identity_key
from server_modules.normalization.url_utils import canonical_page_url


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def store_series_index(
    connect_db: ConnectDb,
    refresh_key: str,
    entries: list[Book],
) -> tuple[int, int]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    canonical_entries = {
        canonical_page_url(entry.url): entry.title
        for entry in entries
        if entry.title and entry.url
    }
    if not canonical_entries:
        raise ValueError("The JNovels series index did not contain any entries.")
    with connect_db() as connection:
        existing_urls = {
            row["url"]
            for row in connection.execute(
                "SELECT url FROM series_index_entries"
            ).fetchall()
        }
        connection.execute("UPDATE series_index_entries SET active = 0")
        connection.executemany(
            """
            INSERT INTO series_index_entries (
                url, title, first_seen_at, last_seen_at, active
            )
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                last_seen_at = excluded.last_seen_at,
                active = 1
            """,
            [
                (url, title, now, now)
                for url, title in canonical_entries.items()
            ],
        )
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (refresh_key, now),
        )
    return len(canonical_entries), len(set(canonical_entries) - existing_urls)


def series_index_is_initialized(connect_db: ConnectDb) -> bool:
    with connect_db() as connection:
        return connection.execute(
            "SELECT 1 FROM series_index_entries LIMIT 1"
        ).fetchone() is not None


def series_index_refresh_due(
    connect_db: ConnectDb,
    refresh_key: str,
    refresh_hours: int,
) -> bool:
    raw = get_metadata(connect_db).get(refresh_key)
    if not raw:
        return True
    try:
        checked_at = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at <= datetime.now(timezone.utc) - timedelta(hours=refresh_hours)


def get_series_index_candidates(
    connect_db: ConnectDb,
    limit: int,
) -> list[Book]:
    if limit <= 0:
        return []
    with connect_db() as connection:
        index_rows = connection.execute(
            """
            SELECT title, url, first_seen_at
            FROM series_index_entries
            WHERE active = 1
            """
        ).fetchall()
        book_rows = connection.execute(
            "SELECT title, url, original_post_url FROM books"
        ).fetchall()
        volume_series_urls = {
            canonical_page_url(row["series_url"])
            for row in connection.execute(
                "SELECT DISTINCT series_url FROM series_volumes"
            ).fetchall()
        }

    cached_urls = volume_series_urls
    cached_identity_keys: set[str] = set()
    for row in book_rows:
        cached_urls.add(canonical_page_url(row["url"]))
        if row["original_post_url"]:
            cached_urls.add(canonical_page_url(row["original_post_url"]))
        key = series_identity_key(row["title"], row["url"])
        if key:
            cached_identity_keys.add(key)

    candidates = [
        (
            series_identity_key(row["title"], row["url"]) in cached_identity_keys,
            row["first_seen_at"],
            row["title"].casefold(),
            Book(row["title"], row["url"], None, None),
        )
        for row in index_rows
        if canonical_page_url(row["url"]) not in cached_urls
    ]
    candidates.sort(key=lambda item: item[2])
    candidates.sort(key=lambda item: item[1], reverse=True)
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[3] for item in candidates[:limit]]


def count_missing_series_index_entries(
    connect_db: ConnectDb,
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    def count(active_connection: sqlite3.Connection) -> int:
        cached_urls = {
            canonical_page_url(row["url"])
            for row in active_connection.execute("SELECT url FROM books")
        }
        cached_urls.update(
            canonical_page_url(row["original_post_url"])
            for row in active_connection.execute(
                """
                SELECT DISTINCT original_post_url
                FROM books
                WHERE original_post_url IS NOT NULL
                """
            )
        )
        cached_urls.update(
            canonical_page_url(row["series_url"])
            for row in active_connection.execute(
                "SELECT DISTINCT series_url FROM series_volumes"
            )
        )
        return sum(
            canonical_page_url(row["url"]) not in cached_urls
            for row in active_connection.execute(
                "SELECT url FROM series_index_entries WHERE active = 1"
            )
        )

    if connection is not None:
        return count(connection)
    with connect_db() as active_connection:
        return count(active_connection)


def add_series_index_candidates(
    connect_db: ConnectDb,
    upsert_books: Callable[[list[Book]], int],
    invalidate_book_filter_counts: Callable[[], None],
    books: list[Book],
) -> list[str]:
    if not books:
        return []
    upsert_books(books)
    urls = [canonical_page_url(book.url) for book in books]
    placeholders = ", ".join("?" for _ in urls)
    with connect_db() as connection:
        connection.execute(
            f"""
            UPDATE books
            SET entry_type = 'series'
            WHERE url IN ({placeholders})
              AND detail_cached_at IS NULL
            """,
            urls,
        )
    invalidate_book_filter_counts()
    return urls
