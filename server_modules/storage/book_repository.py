from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.domain.models import Book
from server_modules.normalization.title_utils import book_duplicate_key, book_page_format


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def upsert_books(
    connect_db: ConnectDb,
    invalidate_book_filter_counts: Callable[[], None],
    invalidate_catalog_indexes: Callable[[], None],
    books: list[Book],
    source_page: int | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        existing_rows = connection.execute(
            "SELECT title, url, original_post_url FROM books"
        ).fetchall()
        preferred_keys = {
            book_duplicate_key(row["title"], row["url"])
            for row in existing_rows
            if book_page_format(row["title"], row["url"]) in {"epub", "mixed"}
        }
        preferred_keys.update(
            book_duplicate_key(book.title, book.url)
            for book in books
            if book_page_format(book.title, book.url) in {"epub", "mixed"}
        )
        books = [
            book
            for book in books
            if book_page_format(book.title, book.url) != "pdf"
            or book_duplicate_key(book.title, book.url) not in preferred_keys
        ]
        before = connection.total_changes
        connection.executemany(
            """
            INSERT INTO books (
                title, url, image_url, published_at, cached_at, last_seen_at,
                source_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                image_url = excluded.image_url,
                published_at = excluded.published_at,
                last_seen_at = excluded.last_seen_at,
                source_order = COALESCE(excluded.source_order, books.source_order)
            """,
            [
                (
                    book.title,
                    book.url,
                    book.image_url,
                    book.published_at,
                    now,
                    now,
                    source_page * 1000 + position if source_page is not None else None,
                )
                for position, book in enumerate(books)
            ],
        )
        prune_pdf_duplicates(
            connect_db,
            invalidate_book_filter_counts,
            invalidate_catalog_indexes,
            connection=connection,
        )
        changes = connection.total_changes - before
    if changes:
        invalidate_catalog_indexes()
    return changes


def delete_book_urls(
    connection: sqlite3.Connection,
    invalidate_book_filter_counts: Callable[[], None],
    urls: set[str],
) -> int:
    if not urls:
        return 0
    placeholders = ", ".join("?" for _ in urls)
    params = tuple(sorted(urls))
    connection.execute(
        f"DELETE FROM series_volumes WHERE series_url IN ({placeholders})",
        params,
    )
    connection.execute(
        f"DELETE FROM series_status WHERE series_url IN ({placeholders})",
        params,
    )
    before = connection.total_changes
    connection.execute(
        f"DELETE FROM books WHERE url IN ({placeholders})",
        params,
    )
    deleted = connection.total_changes - before
    if deleted:
        invalidate_book_filter_counts()
    return deleted


def prune_pdf_duplicates(
    connect_db: ConnectDb,
    invalidate_book_filter_counts: Callable[[], None],
    invalidate_catalog_indexes: Callable[[], None],
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    def prune(active_connection: sqlite3.Connection) -> int:
        rows = active_connection.execute(
            "SELECT title, url, original_post_url FROM books"
        ).fetchall()
        preferred_keys = {
            book_duplicate_key(row["title"], row["url"])
            for row in rows
            if book_page_format(row["title"], row["url"]) in {"epub", "mixed"}
        }
        duplicate_urls = {
            row["url"]
            for row in rows
            if book_page_format(row["title"], row["url"]) == "pdf"
            and book_duplicate_key(row["title"], row["url"]) in preferred_keys
        }
        duplicate_series_urls = {
            row["url"]
            for row in rows
            if row["url"] in duplicate_urls and not row["original_post_url"]
        }
        duplicate_urls.update(
            row["url"]
            for row in rows
            if row["original_post_url"] in duplicate_series_urls
            and book_page_format(row["title"], row["url"]) == "pdf"
        )
        deleted = delete_book_urls(
            active_connection,
            invalidate_book_filter_counts,
            duplicate_urls,
        )
        if deleted:
            invalidate_catalog_indexes()
        return deleted

    if connection is not None:
        return prune(connection)
    with connect_db() as active_connection:
        return prune(active_connection)
