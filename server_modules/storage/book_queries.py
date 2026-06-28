from __future__ import annotations

import re
import sqlite3
import threading
import urllib.parse
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from server_modules.config import SHARED_INDEX_SUBSERIES
from server_modules.normalization.label_utils import catalog_display_label, extract_catalog_label, prefer_catalog_volume_badge
from server_modules.normalization.release_groups import format_release_family
from server_modules.normalization.text_utils import decode_string_list
from server_modules.normalization.title_utils import (
    is_distinct_subseries_title,
    parse_book_identity,
    qualify_numbered_subseries_badge,
    series_identity_key,
)
from server_modules.normalization.url_utils import canonical_page_url, is_internal_jnovels_url


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
    terms: list[str] = []
    for match in re.finditer(r'"([^"]+)"|([\w%\\]+)', query, flags=re.UNICODE):
        quoted, unquoted = match.groups()
        term = " ".join(quoted.split()) if quoted is not None else unquoted
        if term:
            terms.append(term)
    return terms


def fts_query_for_term(term: str) -> str | None:
    tokens = re.findall(r"\w+", term, flags=re.UNICODE)
    if not tokens:
        return None
    escaped_tokens = [token.replace('"', '""') for token in tokens]
    if len(escaped_tokens) == 1:
        return f'"{escaped_tokens[0]}"'
    return f'"{" ".join(escaped_tokens)}"'


def book_fts_filter(
    term: str,
    *,
    include_family_titles: bool,
) -> tuple[str, tuple[str, ...]] | None:
    fts_query = fts_query_for_term(term)
    if not fts_query:
        return None
    conditions = [
        """
        books.id IN (
            SELECT rowid
            FROM books_fts
            WHERE books_fts MATCH ?
        )
        """
    ]
    params = [fts_query]
    if include_family_titles:
        conditions.extend(
            [
                """
                EXISTS (
                    SELECT 1
                    FROM books AS parent
                    WHERE parent.url = books.original_post_url
                      AND parent.id IN (
                          SELECT rowid
                          FROM books_fts
                          WHERE books_fts MATCH ?
                      )
                )
                """,
                """
                EXISTS (
                    SELECT 1
                    FROM books AS sibling
                    WHERE sibling.original_post_url =
                          COALESCE(books.original_post_url, books.url)
                      AND sibling.id IN (
                          SELECT rowid
                          FROM books_fts
                          WHERE books_fts MATCH ?
                      )
                )
                """,
            ]
        )
        params.extend([fts_query, fts_query])
    return f"({' OR '.join(conditions)})", tuple(params)


def book_filter(
    query: str,
    entry_filter: str = "all",
    exact_url: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    if exact_url:
        return " WHERE books.url = ?", (exact_url,)

    conditions: list[str] = []
    params: list[str] = []
    if query:
        for term in parse_search_terms(query):
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if term.isdecimal():
                if books_fts_available():
                    fts_filter = book_fts_filter(term, include_family_titles=False)
                    if fts_filter:
                        condition, condition_params = fts_filter
                        conditions.append(condition)
                        params.extend(condition_params)
                conditions.append(
                    """
                    (
                        books.title LIKE ? ESCAPE '\\'
                        OR books.associated_names LIKE ? ESCAPE '\\'
                    )
                    """
                )
                params.extend([f"%{escaped}%"] * 2)
                continue
            if books_fts_available():
                fts_filter = book_fts_filter(term, include_family_titles=True)
                if fts_filter:
                    condition, condition_params = fts_filter
                    conditions.append(condition)
                    params.extend(condition_params)
            conditions.append(
                """
                (
                    books.title LIKE ? ESCAPE '\\'
                    OR books.associated_names LIKE ? ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1
                        FROM books AS parent
                        WHERE parent.url = books.original_post_url
                          AND parent.title LIKE ? ESCAPE '\\'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM books AS sibling
                        WHERE sibling.original_post_url =
                              COALESCE(books.original_post_url, books.url)
                          AND sibling.title LIKE ? ESCAPE '\\'
                    )
                )
                """
            )
            params.extend([f"%{escaped}%"] * 4)

    preference_series_url = "COALESCE(books.original_post_url, books.url)"
    if entry_filter == "hidden":
        conditions.append(
            f"""
            EXISTS (
                SELECT 1
                FROM series_preferences AS preference
                WHERE preference.series_url = {preference_series_url}
                  AND preference.hidden = 1
            )
            """
        )
    else:
        conditions.append(
            f"""
            NOT EXISTS (
                SELECT 1
                FROM series_preferences AS preference
                WHERE preference.series_url = {preference_series_url}
                  AND preference.hidden = 1
            )
            """
        )

    if entry_filter == "favorites":
        conditions.append(
            f"""
            EXISTS (
                SELECT 1
                FROM series_preferences AS preference
                WHERE preference.series_url = {preference_series_url}
                  AND preference.favorite = 1
            )
            """
        )
    elif entry_filter == "series":
        conditions.append("entry_type = 'series'")
    elif entry_filter == "volumes":
        conditions.append("entry_type = 'volume'")
    elif entry_filter == "pending":
        conditions.append(
            """
            (
                detail_cached_at IS NULL
                OR entry_type IS NULL
                OR download_url IS NULL
            )
            """
        )

    if not conditions:
        return "", ()
    return f" WHERE {' AND '.join(conditions)}", tuple(params)


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
    counts = {
        "all": row["all_count"] or 0,
        "series": row["series_count"] or 0,
        "volumes": row["volume_count"] or 0,
        "pending": row["pending_count"] or 0,
        "favorites": row["favorite_count"] or 0,
        "hidden": row["hidden_count"] or 0,
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

    books = [dict(row) for row in rows]
    (
        catalog_index,
        download_catalog_index,
        volume_counts,
        download_category_counts,
        primary_category_counts,
    ) = get_catalog_indexes()
    aggregate_parent_urls = infer_aggregate_catalog_parents_from_db(
        [
            (book["url"], book["title"])
            for book in books
            if book["entry_type"] == "series"
        ]
    )
    for book in books:
        book["associated_names"] = decode_string_list(book["associated_names"])
        book["genres"] = decode_string_list(book["genres"])
        book["is_favorite"] = bool(book["is_favorite"])
        book["is_hidden"] = bool(book["is_hidden"])
        series_title, volume_label = parse_book_identity(book["title"], book["url"])
        if book["parent_series_title"]:
            series_title, _ = parse_book_identity(
                book["parent_series_title"],
                book["original_post_url"] or book["url"],
            )
        if book["latest_series_title"]:
            latest_series_title, latest_volume_label = parse_book_identity(
                book["latest_series_title"],
                book["original_post_url"] or book["url"],
            )
            if latest_volume_label and not is_distinct_subseries_title(
                series_title,
                latest_series_title,
            ):
                series_title = latest_series_title
        volume_label = prefer_catalog_volume_badge(
            volume_label,
            book["catalog_volume_label"],
        )
        volume_label = qualify_numbered_subseries_badge(
            book["title"],
            volume_label,
        )
        series_url = (
            book["original_post_url"]
            if book["entry_type"] == "volume"
            else book["url"] if book["entry_type"] == "series" else None
        )
        aggregate_parent_url = aggregate_parent_urls.get(book["url"])
        if aggregate_parent_url:
            series_url = aggregate_parent_url
        catalog = catalog_index.get(series_identity_key(book["title"], book["url"]))
        download_catalog = download_catalog_index.get(book["download_url"] or "")
        book_key = series_identity_key(book["title"], book["url"])
        may_infer_catalog = not series_url or volume_counts.get(series_url, 0) <= 1
        if (
            may_infer_catalog
            and download_catalog
            and download_catalog[1] > volume_counts.get(series_url or "", 0)
        ):
            series_url = download_catalog[0]
        if (
            may_infer_catalog
            and catalog
            and catalog[1] > volume_counts.get(series_url or "", 0)
        ):
            series_url = catalog[0]
        book["series_title"] = series_title
        book["series_url"] = series_url
        book["series_page_url"] = SHARED_INDEX_SUBSERIES.get(
            book_key,
            series_url if series_url and is_internal_jnovels_url(series_url) else None,
        )
        book["is_aggregate_catalog"] = bool(aggregate_parent_url)
        book["volume_count"] = volume_counts.get(series_url or "", 0)
        if book["entry_type"] == "series":
            volume_label = None
        book["volume_label"] = volume_label
        primary_catalog_count = primary_category_counts.get(
            series_url or "",
            book["volume_count"],
        )
        book["catalog_count"] = download_category_counts.get(
            book["download_url"] or "",
            primary_catalog_count,
        )
        title_and_url = f"{book['title']} {book['url']}".casefold()
        book["catalog_kind"] = (
            "downloads"
            if "webnovel" in title_and_url
            or "[pdf]" in title_and_url
            or "[epub]" in title_and_url
            or "pdfepub" in title_and_url
            else "volumes"
        )
        book["is_standalone"] = (
            book["entry_type"] == "series" and book["volume_count"] == 1
        )
    return books


def list_series_volumes(series_url: str) -> list[dict[str, Any]]:
    def add_book_rows(
        collected: dict[str, sqlite3.Row],
        connection: sqlite3.Connection,
        sql: str,
        params: tuple[Any, ...],
    ) -> None:
        for row in connection.execute(sql, params).fetchall():
            collected.setdefault(row["url"], row)

    def url_lookup_variants(url: str) -> set[str]:
        canonical = canonical_page_url(url)
        variants = {url, canonical}
        parsed = urllib.parse.urlsplit(canonical)
        if parsed.path != "/" and not parsed.path.endswith("/"):
            variants.add(
                urllib.parse.urlunsplit(parsed._replace(path=f"{parsed.path}/"))
            )
        return variants

    def chunks(values: list[str], size: int = 400) -> Iterator[list[str]]:
        for index in range(0, len(values), size):
            yield values[index : index + size]

    with connect_db() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT label, download_url, position, cached_at, release_group
                FROM series_volumes
                WHERE series_url = ?
                ORDER BY position
                """,
                (series_url,),
            ).fetchall()
        ]
        series_book = connection.execute(
            """
            SELECT title, url, download_url, published_at, entry_type,
                   original_post_url
            FROM books
            WHERE url = ?
            """,
            (series_url,),
        ).fetchone()
        book_rows_by_url: dict[str, sqlite3.Row] = {}
        add_book_rows(
            book_rows_by_url,
            connection,
            """
            SELECT title, url, download_url, published_at, entry_type,
                   original_post_url
            FROM books
            WHERE original_post_url = ?
            """,
            (series_url,),
        )
        download_urls = sorted(
            {row["download_url"] for row in rows if row["download_url"]}
        )
        for batch in chunks(download_urls):
            placeholders = ", ".join("?" for _ in batch)
            add_book_rows(
                book_rows_by_url,
                connection,
                f"""
                SELECT title, url, download_url, published_at, entry_type,
                       original_post_url
                FROM books
                WHERE download_url IN ({placeholders})
                """,
                tuple(batch),
            )
        internal_url_variants = sorted(
            {
                variant
                for download_url in download_urls
                if is_internal_jnovels_url(download_url)
                for variant in url_lookup_variants(download_url)
            }
        )
        for batch in chunks(internal_url_variants):
            placeholders = ", ".join("?" for _ in batch)
            add_book_rows(
                book_rows_by_url,
                connection,
                f"""
                SELECT title, url, download_url, published_at, entry_type,
                       original_post_url
                FROM books
                WHERE url IN ({placeholders})
                """,
                tuple(batch),
            )
        labels = sorted({row["label"] for row in rows if row["label"]})
        for batch in chunks(labels, 80):
            label_conditions = " OR ".join(
                "title LIKE ? ESCAPE '\\'" for _ in batch
            )
            label_params = tuple(
                "%"
                + label.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
                for label in batch
            )
            add_book_rows(
                book_rows_by_url,
                connection,
                f"""
                SELECT title, url, download_url, published_at, entry_type,
                       original_post_url
                FROM books
                WHERE entry_type = 'volume'
                  AND ({label_conditions})
                """,
                label_params,
            )

    target_key = series_identity_key(
        series_book["title"] if series_book else None,
        series_url,
    )
    series_title = (
        parse_book_identity(series_book["title"], series_url)[0]
        if series_book
        else None
    )
    if series_book and "short story collection" in series_book["title"].casefold():
        for volume in rows:
            volume["label"] = re.sub(
                r"^Short Story Collection\s+",
                "",
                volume["label"],
                flags=re.IGNORECASE,
            )
    matches: dict[str, list[sqlite3.Row]] = {}
    family_downloads: dict[str, sqlite3.Row] = {}
    internal_posts: dict[str, sqlite3.Row] = {}
    book_rows = sorted(
        book_rows_by_url.values(),
        key=lambda book: (
            book["entry_type"] == "volume",
            book["published_at"] or "",
            book["url"],
        ),
        reverse=True,
    )
    for book in book_rows:
        internal_posts.setdefault(canonical_page_url(book["url"]), book)
        unrelated = (
            canonical_page_url(book["original_post_url"] or "") !=
            canonical_page_url(series_url)
            and series_identity_key(book["title"], book["url"]) != target_key
        )
        aggregate_parent_url = (
            infer_aggregate_catalog_parent_from_db(book["url"], book["title"])
            if book["entry_type"] == "series"
            else None
        )
        if unrelated and aggregate_parent_url != series_url:
            continue
        is_volume_book = book["entry_type"] == "volume"
        if is_volume_book and book["download_url"]:
            family_downloads.setdefault(book["download_url"], book)
        elif aggregate_parent_url == series_url and book["download_url"]:
            family_downloads.setdefault(book["download_url"], book)
        label = extract_catalog_label(book["title"])
        if is_volume_book and label:
            matches.setdefault(label.casefold(), []).append(book)

    if series_book and series_book["download_url"]:
        family_downloads.setdefault(series_book["download_url"], series_book)

    label_categories: dict[str, set[str]] = {}
    for volume in rows:
        format_family = format_release_family(volume["release_group"])
        category = (
            f"__format__:{format_family}"
            if format_family is not None
            else (volume["release_group"] or "").casefold() or "__default__"
        )
        label_categories.setdefault(volume["label"].casefold(), set()).add(category)

    for volume in rows:
        internal_reference = is_internal_jnovels_url(volume["download_url"])
        volume["is_internal_reference"] = internal_reference
        book = (
            internal_posts.get(canonical_page_url(volume["download_url"]))
            if internal_reference
            else family_downloads.get(volume["download_url"])
        )
        label_key = volume["label"].casefold()
        if (
            book is None
            and not internal_reference
            and len(label_categories.get(label_key, set())) <= 1
        ):
            candidates = matches.get(label_key, [])
            book = max(
                candidates,
                key=lambda candidate: candidate["published_at"] or "",
                default=None,
            )
        volume["published_at"] = book["published_at"] if book else None
        volume["post_url"] = book["url"] if book else None
        volume["display_label"] = catalog_display_label(
            volume["label"],
            series_title,
            volume["release_group"],
        )

    dated_formats: dict[tuple[str, str], dict[str, Any]] = {}
    for volume in rows:
        format_family = format_release_family(volume["release_group"])
        if format_family is not None and volume["published_at"]:
            dated_formats.setdefault(
                (format_family, volume["label"].casefold()),
                volume,
            )
    for volume in rows:
        format_family = format_release_family(volume["release_group"])
        if format_family is None or volume["published_at"]:
            continue
        dated = dated_formats.get((format_family, volume["label"].casefold()))
        if dated:
            volume["published_at"] = dated["published_at"]
            volume["post_url"] = dated["post_url"]
    return filter_pdf_rows_when_epub_exists(rows)
