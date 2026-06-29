from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from server_modules.storage import (
    book_filters,
    book_result_projection,
    series_volume_queries,
)


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]

DB_PATH: Path | None = None
_book_filter_counts_lock = threading.Lock()
_book_filter_counts_cache: tuple[Path, dict[str, int]] | None = None
_connect_db: ConnectDb | None = None
_books_fts_available: Callable[[], bool] | None = None
_get_catalog_indexes: Callable[[], tuple[
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]] | None = None
_infer_aggregate_catalog_parents_from_db: Callable[[list[tuple[str, str]]], dict[str, str]] | None = None
_infer_aggregate_catalog_parent_from_db: Callable[[str, str], str | None] | None = None
_filter_pdf_rows_when_epub_exists: Callable[[list[Any]], list[Any]] | None = None


def configure(
    *,
    db_path: Path,
    connect_db: ConnectDb,
    books_fts_available: Callable[[], bool],
    get_catalog_indexes: Callable[[], tuple[
        dict[str, tuple[str, int]],
        dict[str, tuple[str, int]],
        dict[str, int],
        dict[str, int],
        dict[str, int],
    ]],
    infer_aggregate_catalog_parents_from_db: Callable[[list[tuple[str, str]]], dict[str, str]],
    infer_aggregate_catalog_parent_from_db: Callable[[str, str], str | None],
    filter_pdf_rows_when_epub_exists: Callable[[list[Any]], list[Any]],
) -> None:
    global DB_PATH
    global _connect_db
    global _books_fts_available
    global _get_catalog_indexes
    global _infer_aggregate_catalog_parents_from_db
    global _infer_aggregate_catalog_parent_from_db
    global _filter_pdf_rows_when_epub_exists
    DB_PATH = db_path
    _connect_db = connect_db
    _books_fts_available = books_fts_available
    _get_catalog_indexes = get_catalog_indexes
    _infer_aggregate_catalog_parents_from_db = infer_aggregate_catalog_parents_from_db
    _infer_aggregate_catalog_parent_from_db = infer_aggregate_catalog_parent_from_db
    _filter_pdf_rows_when_epub_exists = filter_pdf_rows_when_epub_exists


def connect_db() -> AbstractContextManager[sqlite3.Connection]:
    if _connect_db is None:
        raise RuntimeError("book query dependencies are not configured")
    return _connect_db()


def books_fts_available() -> bool:
    if _books_fts_available is None:
        raise RuntimeError("book query dependencies are not configured")
    return _books_fts_available()


def get_catalog_indexes() -> tuple[
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]:
    if _get_catalog_indexes is None:
        raise RuntimeError("book query dependencies are not configured")
    return _get_catalog_indexes()


def infer_aggregate_catalog_parents_from_db(
    books: list[tuple[str, str]],
) -> dict[str, str]:
    if _infer_aggregate_catalog_parents_from_db is None:
        raise RuntimeError("book query dependencies are not configured")
    return _infer_aggregate_catalog_parents_from_db(books)


def infer_aggregate_catalog_parent_from_db(url: str, title: str) -> str | None:
    if _infer_aggregate_catalog_parent_from_db is None:
        raise RuntimeError("book query dependencies are not configured")
    return _infer_aggregate_catalog_parent_from_db(url, title)


def filter_pdf_rows_when_epub_exists(rows: list[Any]) -> list[Any]:
    if _filter_pdf_rows_when_epub_exists is None:
        raise RuntimeError("book query dependencies are not configured")
    return _filter_pdf_rows_when_epub_exists(rows)


def invalidate_book_filter_counts() -> None:
    global _book_filter_counts_cache
    _book_filter_counts_cache = None


def parse_search_terms(query: str) -> list[str]:
    return book_filters.parse_search_terms(query)


def fts_query_for_term(term: str) -> str | None:
    return book_filters.fts_query_for_term(term)


def book_fts_filter(
    term: str,
    *,
    include_family_titles: bool,
) -> tuple[str, tuple[str, ...]] | None:
    return book_filters.book_fts_filter(
        term,
        include_family_titles=include_family_titles,
    )


def book_filter(
    query: str,
    entry_filter: str = "all",
    exact_url: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    return book_filters.book_filter(
        query,
        entry_filter,
        exact_url,
        books_fts_available=books_fts_available,
    )


def count_books(
    query: str,
    entry_filter: str = "all",
    exact_url: str | None = None,
) -> int:
    where_clause, params = book_filter(query, entry_filter, exact_url)
    with connect_db() as connection:
        return connection.execute(
            f"SELECT COUNT(*) FROM books{where_clause}",
            params,
        ).fetchone()[0]


def count_book_filters() -> dict[str, int]:
    global _book_filter_counts_cache
    assert DB_PATH is not None
    with _book_filter_counts_lock:
        if (
            _book_filter_counts_cache is not None
            and _book_filter_counts_cache[0] == DB_PATH
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
        _book_filter_counts_cache = (DB_PATH, counts)
    return dict(counts)


def list_books(
    query: str,
    sort: str,
    limit: int | None = None,
    offset: int = 0,
    entry_filter: str = "all",
    exact_url: str | None = None,
) -> list[dict[str, Any]]:
    order_by = {
        "published_desc": (
            "published_at IS NULL, published_at DESC, "
            "source_order IS NULL, source_order ASC, id ASC"
        ),
        "published_asc": (
            "published_at IS NULL, published_at ASC, "
            "source_order IS NULL, source_order DESC, id DESC"
        ),
        "title_asc": "title COLLATE NOCASE ASC, id ASC",
        "title_desc": "title COLLATE NOCASE DESC, id DESC",
        "cached_desc": "cached_at DESC, title COLLATE NOCASE, id DESC",
    }.get(
        sort,
        "published_at IS NULL, published_at DESC, "
        "source_order IS NULL, source_order ASC, id ASC",
    )

    where_clause, filter_params = book_filter(query, entry_filter, exact_url)
    sql = """
        SELECT id, title, url, image_url, published_at, cached_at, last_seen_at,
               source_order,
               entry_type, original_post_url, download_url, detail_cached_at,
               associated_names, genres, synopsis,
               EXISTS (
                   SELECT 1
                   FROM series_preferences AS preference
                   WHERE preference.series_url =
                         COALESCE(books.original_post_url, books.url)
                     AND preference.favorite = 1
               ) AS is_favorite,
               EXISTS (
                   SELECT 1
                   FROM series_preferences AS preference
                   WHERE preference.series_url =
                         COALESCE(books.original_post_url, books.url)
                     AND preference.hidden = 1
               ) AS is_hidden,
                (
                    SELECT sibling.title
                   FROM books AS sibling
                   WHERE sibling.original_post_url =
                         COALESCE(books.original_post_url, books.url)
                   ORDER BY sibling.published_at IS NULL,
                            sibling.published_at DESC,
                            sibling.id DESC
                    LIMIT 1
                 ) AS latest_series_title,
               (
                  SELECT parent.title
                  FROM books AS parent
                  WHERE parent.url =
                        COALESCE(books.original_post_url, books.url)
                  LIMIT 1
               ) AS parent_series_title,
               (
                  SELECT parent.download_url
                  FROM books AS parent
                  WHERE parent.url =
                        COALESCE(books.original_post_url, books.url)
                  LIMIT 1
               ) AS parent_series_download,
                 COALESCE(
                    (
                        SELECT own_catalog_volume.label
                        FROM series_volumes AS own_catalog_volume
                        WHERE own_catalog_volume.download_url = books.download_url
                          AND own_catalog_volume.series_url =
                              COALESCE(books.original_post_url, books.url)
                        ORDER BY own_catalog_volume.position
                        LIMIT 1
                    ),
                    (
                        SELECT matching_catalog_volume.label
                        FROM series_volumes AS matching_catalog_volume
                        WHERE matching_catalog_volume.download_url =
                              books.download_url
                        ORDER BY matching_catalog_volume.position
                        LIMIT 1
                    )
                ) AS catalog_volume_label
        FROM books
    """ + where_clause
    params: tuple[Any, ...] = filter_params
    sql += f" ORDER BY {order_by}"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += (limit, offset)

    with connect_db() as connection:
        rows = connection.execute(sql, params).fetchall()

    return book_result_projection.project_listed_books(
        rows,
        get_catalog_indexes=get_catalog_indexes,
        infer_aggregate_catalog_parents_from_db=infer_aggregate_catalog_parents_from_db,
    )



def list_series_volumes(series_url: str) -> list[dict[str, Any]]:
    return series_volume_queries.list_series_volumes(
        series_url,
        connect_db=connect_db,
        infer_aggregate_catalog_parent_from_db=infer_aggregate_catalog_parent_from_db,
        filter_pdf_rows_when_epub_exists=filter_pdf_rows_when_epub_exists,
    )
