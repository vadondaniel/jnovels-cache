from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]

_book_filter_counts_lock = threading.Lock()
_book_filter_counts_cache: tuple[Path, dict[str, int]] | None = None


def invalidate_book_filter_counts() -> None:
    global _book_filter_counts_cache
    _book_filter_counts_cache = None


def count_book_filters(
    *,
    connect_db: ConnectDb,
    db_path: Path,
) -> dict[str, int]:
    global _book_filter_counts_cache
    with _book_filter_counts_lock:
        if (
            _book_filter_counts_cache is not None
            and _book_filter_counts_cache[0] == db_path
        ):
            return dict(_book_filter_counts_cache[1])

    with connect_db() as connection:
        row = connection.execute(
            """
            SELECT SUM(NOT EXISTS (
                       SELECT 1
                       FROM series_preferences AS preference
                       WHERE preference.series_url =
                             COALESCE(books.original_post_url, books.url)
                         AND preference.hidden = 1
                   )) AS all_count,
                   SUM(
                       entry_type = 'series'
                       AND NOT EXISTS (
                           SELECT 1
                           FROM series_preferences AS preference
                           WHERE preference.series_url =
                                 COALESCE(books.original_post_url, books.url)
                             AND preference.hidden = 1
                       )
                   ) AS series_count,
                   SUM(
                       entry_type = 'volume'
                       AND NOT EXISTS (
                           SELECT 1
                           FROM series_preferences AS preference
                           WHERE preference.series_url =
                                 COALESCE(books.original_post_url, books.url)
                             AND preference.hidden = 1
                       )
                   ) AS volume_count,
                   SUM(
                       (
                           detail_cached_at IS NULL
                           OR entry_type IS NULL
                           OR download_url IS NULL
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM series_preferences AS preference
                           WHERE preference.series_url =
                                 COALESCE(books.original_post_url, books.url)
                             AND preference.hidden = 1
                       )
                   ) AS pending_count
                   ,SUM(
                       EXISTS (
                           SELECT 1
                           FROM series_preferences AS preference
                           WHERE preference.series_url =
                                 COALESCE(books.original_post_url, books.url)
                             AND preference.favorite = 1
                             AND preference.hidden = 0
                       )
                   ) AS favorite_count
                   ,SUM(
                       EXISTS (
                           SELECT 1
                           FROM series_preferences AS preference
                           WHERE preference.series_url =
                                 COALESCE(books.original_post_url, books.url)
                             AND preference.hidden = 1
                       )
                   ) AS hidden_count
            FROM books
            """
        ).fetchone()
    counts: dict[str, int] = {
        "all": int(row["all_count"] or 0),
        "series": int(row["series_count"] or 0),
        "volumes": int(row["volume_count"] or 0),
        "pending": int(row["pending_count"] or 0),
        "favorites": int(row["favorite_count"] or 0),
        "hidden": int(row["hidden_count"] or 0),
    }
    with _book_filter_counts_lock:
        _book_filter_counts_cache = (db_path, counts)
    return dict(counts)
