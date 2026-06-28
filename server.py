from __future__ import annotations

import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from server_modules.web.app_runner import run_app
from server_modules.storage import aggregate_catalogs
from server_modules.storage import book_queries
from server_modules.storage import book_repository
from server_modules.storage import catalog_aliases
from server_modules.storage import catalog_cache
from server_modules.storage import cache_state
from server_modules.storage import detail_queue
from server_modules.storage import fallback_catalogs
from server_modules.storage import metadata_store
from server_modules.storage import series_parent_inference
from server_modules.storage import series_index_store
from server_modules.storage import series_catalog_repository
from server_modules.sync.collection import collect_sync_books as collect_sync_books_impl
from server_modules.normalization.label_utils import (
    catalog_display_label,
    catalog_label_with_subtitle,
    compact_catalog_volume_badge,
    compact_volume_badge,
    extract_catalog_label,
    extract_named_release_label,
    extract_sao_volume_label,
    extract_trailing_catalog_label,
    extract_volume_label,
    is_volume_label,
    legacy_anchor_label,
    looks_like_download_text,
    normalize_volume_designator,
    normalize_volume_number,
    prefer_catalog_volume_badge,
    roman_to_integer,
)
from server_modules.storage.db_schema import initialize_schema
from server_modules.web.fetchers import (
    fetch_page,
    fetch_post_details,
    fetch_series_index,
    search_url,
)
from server_modules.config import (
    AUTO_REPAIR_LIMIT,
    AUTO_REPAIR_MAX_ATTEMPTS,
    AUTO_SERIES_INDEX_LIMIT,
    AUTO_SERIES_REFRESH_LIMIT,
    AUTO_SYNC_COOLDOWN_SECONDS,
    BASE_URL,
    CATALOG_RECONCILE_KEY,
    DB_PATH,
    DEFAULT_PAGE_SIZE,
    EXCLUDED_SEARCH_PATHS,
    LEGACY_REPARSE_KEY,
    LEGACY_VOLUME_RECONCILE_KEY,
    MAX_PAGE_SIZE,
    MERGED_CATALOG_GROUP_OVERRIDES,
    PARTIAL_GROUP_PRESERVE_SERIES,
    ROOT,
    SAO_INDEX_URL,
    SERIES_INDEX_REFRESH_HOURS,
    SERIES_INDEX_REFRESH_KEY,
    SERIES_INDEX_URL,
    SERIES_METADATA_PARSER_KEY,
    SERIES_METADATA_PARSER_VERSION,
    SERIES_RELEASE_GROUP_OVERRIDES,
    SERIES_STALE_DAYS,
    SHARED_INDEX_SUBSERIES,
    STATIC_DIR,
    SYNC_CHECKPOINT_KEY,
    USER_AGENT,
)
from server_modules.domain.models import Book, PostDetails, SeriesVolume
from server_modules.parsers.post_details import PostDetailsParser
from server_modules.normalization.release_groups import (
    catalog_section_from_heading,
    default_catalog_section_from_url,
    format_release_family,
    is_format_release_group,
    qualify_format_release_group,
    release_group_format_kind,
    release_group_from_catalog_text,
    release_group_from_heading,
)
from server_modules.parsers.search_results import SearchResultsParser, normalize_date
from server_modules.parsers.series_index import SeriesIndexParser
from server_modules.normalization.title_utils import (
    book_page_format,
    is_distinct_subseries_title,
    is_named_subseries_title,
    is_numbered_subseries_title,
    normalize_display_title,
    parse_book_identity,
    qualify_numbered_subseries_badge,
    series_identity_key,
    virtual_series_url,
)
from server_modules.normalization.text_utils import (
    decode_string_list,
    encode_string_list,
    normalize_legacy_title_case,
    normalize_synopsis,
    unique_nonempty_lines,
)
from server_modules.normalization.url_utils import (
    canonical_page_url,
    is_counted_catalog_volume,
    is_external_http_url,
    is_external_site_root,
    is_internal_jnovels_url,
    is_internal_related_catalog_url,
    is_legacy_download_url,
    is_legacy_forum_landing_url,
)
from server_modules.web.handlers import create_app_handler

__all__ = [
    "AUTO_REPAIR_LIMIT",
    "AUTO_REPAIR_MAX_ATTEMPTS",
    "AUTO_SERIES_INDEX_LIMIT",
    "AUTO_SERIES_REFRESH_LIMIT",
    "AUTO_SYNC_COOLDOWN_SECONDS",
    "BASE_URL",
    "Book",
    "CATALOG_RECONCILE_KEY",
    "DB_PATH",
    "DEFAULT_PAGE_SIZE",
    "EXCLUDED_SEARCH_PATHS",
    "LEGACY_REPARSE_KEY",
    "LEGACY_VOLUME_RECONCILE_KEY",
    "MAX_PAGE_SIZE",
    "MERGED_CATALOG_GROUP_OVERRIDES",
    "PARTIAL_GROUP_PRESERVE_SERIES",
    "PostDetails",
    "PostDetailsParser",
    "ROOT",
    "SAO_INDEX_URL",
    "SERIES_INDEX_REFRESH_HOURS",
    "SERIES_INDEX_REFRESH_KEY",
    "SERIES_INDEX_URL",
    "SERIES_METADATA_PARSER_KEY",
    "SERIES_METADATA_PARSER_VERSION",
    "SERIES_RELEASE_GROUP_OVERRIDES",
    "SERIES_STALE_DAYS",
    "SHARED_INDEX_SUBSERIES",
    "STATIC_DIR",
    "SYNC_CHECKPOINT_KEY",
    "SearchResultsParser",
    "SeriesIndexParser",
    "SeriesVolume",
    "USER_AGENT",
    "catalog_display_label",
    "catalog_label_with_subtitle",
    "catalog_section_from_heading",
    "compact_catalog_volume_badge",
    "compact_volume_badge",
    "default_catalog_section_from_url",
    "prefer_catalog_volume_badge",
    "is_distinct_subseries_title",
    "format_release_family",
    "decode_string_list",
    "extract_catalog_label",
    "extract_named_release_label",
    "extract_sao_volume_label",
    "extract_trailing_catalog_label",
    "extract_volume_label",
    "fetch_page",
    "fetch_post_details",
    "fetch_series_index",
    "is_external_http_url",
    "is_external_site_root",
    "is_format_release_group",
    "is_internal_related_catalog_url",
    "is_legacy_download_url",
    "is_legacy_forum_landing_url",
    "is_named_subseries_title",
    "is_numbered_subseries_title",
    "is_volume_label",
    "legacy_anchor_label",
    "looks_like_download_text",
    "normalize_date",
    "normalize_display_title",
    "normalize_legacy_title_case",
    "normalize_synopsis",
    "normalize_volume_designator",
    "normalize_volume_number",
    "qualify_format_release_group",
    "qualify_numbered_subseries_badge",
    "release_group_from_catalog_text",
    "release_group_from_heading",
    "roman_to_integer",
    "search_url",
    "unique_nonempty_lines",
]

_catalog_index_lock = threading.Lock()
_catalog_index_cache: tuple[
    Path,
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
] | None = None
_books_fts_lock = threading.Lock()
_books_fts_available: tuple[Path, bool] | None = None


def parse_positive_int(value: str, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default



def is_volume_range_badge(label: str | None) -> bool:
    return aggregate_catalogs.is_volume_range_badge(label)


def is_aggregate_volume_title(title: str, url: str) -> bool:
    return aggregate_catalogs.is_aggregate_volume_title(title, url)


def is_aggregate_volume_catalog(url: str, details: PostDetails) -> bool:
    return aggregate_catalogs.is_aggregate_volume_catalog(url, details)


def infer_aggregate_catalog_parent(url: str, details: PostDetails) -> str | None:
    return aggregate_catalogs.infer_aggregate_catalog_parent(url, details)


def infer_aggregate_catalog_parent_from_db(url: str, title: str) -> str | None:
    return aggregate_catalogs.infer_aggregate_catalog_parent_from_db(url, title)


def infer_aggregate_catalog_parents_from_db(
    candidates: list[tuple[str, str]],
) -> dict[str, str]:
    return aggregate_catalogs.infer_aggregate_catalog_parents_from_db(candidates)


def aggregate_release_group(title: str, details: PostDetails) -> str:
    return aggregate_catalogs.aggregate_release_group(title, details)


def merge_aggregate_catalog_into_parent(
    parent_url: str,
    aggregate_url: str,
    details: PostDetails,
) -> int:
    return aggregate_catalogs.merge_aggregate_catalog_into_parent(
        parent_url,
        aggregate_url,
        details,
    )


def merge_cached_aggregate_catalogs_into_parent(parent_url: str) -> int:
    return aggregate_catalogs.merge_cached_aggregate_catalogs_into_parent(parent_url)



def delete_book_urls(
    connection: sqlite3.Connection,
    urls: set[str],
) -> int:
    return book_repository.delete_book_urls(
        connection,
        invalidate_book_filter_counts,
        urls,
    )


def prune_pdf_duplicates(
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    return book_repository.prune_pdf_duplicates(
        connect_db,
        invalidate_book_filter_counts,
        invalidate_catalog_indexes,
        connection=connection,
    )


def _configure_catalog_cache() -> None:
    catalog_cache.configure(
        db_path=DB_PATH,
        connect_db=connect_db,
        invalidate_book_filter_counts=book_queries.invalidate_book_filter_counts,
    )


def invalidate_book_filter_counts() -> None:
    book_queries.invalidate_book_filter_counts()


def row_value(row: Any, key: str) -> Any | None:
    return catalog_cache.row_value(row, key)


def filter_pdf_rows_when_epub_exists(rows: list[Any]) -> list[Any]:
    return catalog_cache.filter_pdf_rows_when_epub_exists(rows)


def invalidate_catalog_indexes() -> None:
    _configure_catalog_cache()
    catalog_cache.invalidate_catalog_indexes()


def get_catalog_indexes() -> tuple[
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]:
    _configure_catalog_cache()
    return catalog_cache.get_catalog_indexes()


def get_series_catalog_index() -> dict[str, tuple[str, int]]:
    _configure_catalog_cache()
    return catalog_cache.get_series_catalog_index()


def get_download_catalog_index() -> dict[str, tuple[str, int]]:
    _configure_catalog_cache()
    return catalog_cache.get_download_catalog_index()


def set_books_fts_available(available: bool) -> None:
    global _books_fts_available
    with _books_fts_lock:
        _books_fts_available = (DB_PATH, available)


def books_fts_available() -> bool:
    with _books_fts_lock:
        return (
            _books_fts_available is not None
            and _books_fts_available[0] == DB_PATH
            and _books_fts_available[1]
        )


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.create_function("is_internal_jnovels_url", 1, is_internal_jnovels_url)
    connection.create_function(
        "is_counted_catalog_volume",
        2,
        is_counted_catalog_volume,
    )
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_db() -> None:
    invalidate_catalog_indexes()
    with connect_db() as connection:
        set_books_fts_available(initialize_schema(connection))
    build_missing_fallback_catalogs()
    prepare_legacy_reparse()
    prepare_series_metadata_refresh()
    prune_pdf_duplicates()
    cleanup_misclassified_series_tracking()
    reconcile_legacy_volume_entries()
    reconcile_catalog_relationships()


def upsert_books(books: list[Book], source_page: int | None = None) -> int:
    return book_repository.upsert_books(
        connect_db,
        invalidate_book_filter_counts,
        invalidate_catalog_indexes,
        books,
        source_page,
    )


def set_metadata(key: str, value: str) -> None:
    metadata_store.set_metadata(connect_db, key, value)


def delete_metadata(key: str) -> None:
    metadata_store.delete_metadata(connect_db, key)


def get_search_scan_frontier() -> int:
    return metadata_store.get_search_scan_frontier(connect_db)


def get_book_source_orders(urls: list[str]) -> dict[str, int]:
    return metadata_store.get_book_source_orders(connect_db, urls)


def observed_source_page_shift(
    current_page: int,
    source_orders: dict[str, int],
) -> int:
    return metadata_store.observed_source_page_shift(current_page, source_orders)


def apply_observed_front_shift(
    frontier: int,
    applied_shift: int,
    observed_shift: int,
) -> tuple[int, int]:
    return metadata_store.apply_observed_front_shift(
        frontier,
        applied_shift,
        observed_shift,
    )


def get_metadata(
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, str]:
    return metadata_store.get_metadata(connect_db, connection=connection)


def get_public_metadata(
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, str]:
    return metadata_store.get_public_metadata(
        connect_db,
        hidden_keys={
            SYNC_CHECKPOINT_KEY,
            LEGACY_REPARSE_KEY,
            LEGACY_VOLUME_RECONCILE_KEY,
            SERIES_METADATA_PARSER_KEY,
        },
        connection=connection,
    )


def prepare_legacy_reparse() -> int:
    return metadata_store.prepare_legacy_reparse(connect_db, LEGACY_REPARSE_KEY)


def prepare_series_metadata_refresh() -> int:
    return metadata_store.prepare_series_metadata_refresh(
        connect_db,
        SERIES_METADATA_PARSER_KEY,
        SERIES_METADATA_PARSER_VERSION,
    )


def save_sync_checkpoint(checkpoint: dict[str, Any]) -> None:
    metadata_store.save_sync_checkpoint(connect_db, SYNC_CHECKPOINT_KEY, checkpoint)


def load_sync_checkpoint() -> dict[str, Any] | None:
    return metadata_store.load_sync_checkpoint(connect_db, SYNC_CHECKPOINT_KEY)


def clear_sync_checkpoint() -> None:
    metadata_store.clear_sync_checkpoint(connect_db, SYNC_CHECKPOINT_KEY)




def store_series_index(entries: list[Book]) -> tuple[int, int]:
    return series_index_store.store_series_index(
        connect_db,
        SERIES_INDEX_REFRESH_KEY,
        entries,
    )


def series_index_is_initialized() -> bool:
    return series_index_store.series_index_is_initialized(connect_db)


def series_index_refresh_due() -> bool:
    return series_index_store.series_index_refresh_due(
        connect_db,
        SERIES_INDEX_REFRESH_KEY,
        SERIES_INDEX_REFRESH_HOURS,
    )


def get_series_index_candidates(limit: int) -> list[Book]:
    return series_index_store.get_series_index_candidates(connect_db, limit)


def count_missing_series_index_entries(
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    return series_index_store.count_missing_series_index_entries(
        connect_db,
        connection=connection,
    )


def add_series_index_candidates(books: list[Book]) -> list[str]:
    return series_index_store.add_series_index_candidates(
        connect_db,
        upsert_books,
        invalidate_book_filter_counts,
        books,
    )




def infer_series_parent(
    url: str,
    parent_hints: list[str] | None = None,
    download_url: str | None = None,
    volume_label_hint: str | None = None,
) -> str | None:
    return series_parent_inference.infer_series_parent(
        url,
        parent_hints,
        download_url,
        volume_label_hint,
        connect_db=connect_db,
    )


def merge_explicit_alias_catalogs(
    canonical_series_url: str,
    hints: list[str],
    source_url: str,
) -> int:
    return catalog_aliases.merge_explicit_alias_catalogs(
        canonical_series_url,
        hints,
        source_url,
        connect_db=connect_db,
        reparent_series_alias=reparent_series_alias,
    )


def reconcile_legacy_volume_entries(*, force: bool = False) -> int:
    with connect_db() as connection:
        if not force and connection.execute(
            "SELECT 1 FROM metadata WHERE key = ?",
            (LEGACY_VOLUME_RECONCILE_KEY,),
        ).fetchone():
            return 0
        rows = connection.execute(
            """
            SELECT title, url, download_url
            FROM books
            WHERE entry_type = 'series'
            ORDER BY id
            """
        ).fetchall()

    repaired = 0
    for row in rows:
        if is_aggregate_volume_title(row["title"], row["url"]):
            continue
        _, volume_label = parse_book_identity(row["title"], row["url"])
        if not volume_label:
            continue
        parent_url = infer_series_parent(
            row["url"],
            download_url=row["download_url"],
            volume_label_hint=re.sub(
                r"^Vol\.\s+",
                "Volume ",
                volume_label,
                flags=re.IGNORECASE,
            ),
        )
        if not parent_url or not is_cached_series_url(parent_url):
            continue
        with connect_db() as connection:
            connection.execute(
                """
                UPDATE books
                SET entry_type = 'volume',
                    original_post_url = ?
                WHERE url = ?
                """,
                (parent_url, row["url"]),
            )
        reparent_series_alias(row["url"], parent_url)
        repaired += 1

    repaired += group_orphan_volume_families()

    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                LEGACY_VOLUME_RECONCILE_KEY,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return repaired


def group_orphan_volume_families() -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT b.id, b.title, b.url, b.download_url, b.published_at,
                   (
                       SELECT COUNT(*)
                       FROM series_volumes AS sv
                       WHERE sv.series_url = b.url
                   ) AS catalog_count
            FROM books AS b
            WHERE b.entry_type = 'series'
              AND b.download_url IS NOT NULL
            ORDER BY b.published_at IS NULL, b.published_at, b.id
            """
        ).fetchall()

    families: dict[tuple[str, str, bool], list[sqlite3.Row]] = {}
    for row in rows:
        if is_aggregate_volume_title(row["title"], row["url"]):
            continue
        _, volume_label = parse_book_identity(row["title"], row["url"])
        page_format = book_page_format(row["title"], row["url"])
        identity = series_identity_key(row["title"], row["url"])
        if not volume_label or page_format not in {"epub", "pdf", "mixed"} or not identity:
            continue
        is_webnovel = "webnovel" in f"{row['title']} {row['url']}".casefold()
        families.setdefault((identity, page_format, is_webnovel), []).append(row)

    grouped = 0
    for family in families.values():
        if len(family) < 2:
            continue

        def release_number(row: sqlite3.Row) -> float:
            _, badge = parse_book_identity(row["title"], row["url"])
            match = re.search(r"[0-9]+(?:\.[0-9]+)?", badge or "")
            return float(match.group(0)) if match else float("inf")

        canonical = min(
            family,
            key=lambda row: (
                -(row["catalog_count"] > 1),
                release_number(row),
                row["published_at"] is None,
                row["published_at"] or "",
                row["id"],
            ),
        )
        _, canonical_badge = parse_book_identity(
            canonical["title"],
            canonical["url"],
        )
        canonical_label = re.sub(
            r"^Vol\.\s+",
            "Volume ",
            canonical_badge or "Volume 1",
            flags=re.IGNORECASE,
        )
        with connect_db() as connection:
            next_position = connection.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM series_volumes
                WHERE series_url = ?
                """,
                (canonical["url"],),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT OR IGNORE INTO series_volumes
                    (series_url, label, download_url, position, cached_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    canonical["url"],
                    canonical_label,
                    canonical["download_url"],
                    next_position,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        for row in family:
            if row["url"] == canonical["url"]:
                continue
            with connect_db() as connection:
                connection.execute(
                    """
                    UPDATE books
                    SET entry_type = 'volume',
                        original_post_url = ?
                    WHERE url = ?
                    """,
                    (canonical["url"], row["url"]),
                )
            reparent_series_alias(row["url"], canonical["url"])
            grouped += 1
        build_fallback_catalog(canonical["url"])
    return grouped


def update_book_details(url: str, details: PostDetails) -> None:
    inferred_parent: str | None = None
    orphaned_parent: str | None = None
    aggregate_parent: str | None = None
    aggregate_catalog = (
        details.entry_type == "series"
        and not details.original_post_url
        and is_aggregate_volume_catalog(url, details)
    )
    if (
        details.entry_type == "series"
        and not details.original_post_url
        and (aggregate_catalog or not is_established_series_book(url))
    ):
        volume_label_hint = next(
            (
                volume.label
                for volume in details.volumes
                if re.match(r"^Volume\s+[0-9]", volume.label, re.IGNORECASE)
            ),
            None,
        )
        inferred_parent = (
            infer_aggregate_catalog_parent(url, details)
            if aggregate_catalog
            else None
        ) or infer_series_parent(
            url,
            [*details.parent_hints, *details.associated_names],
            details.download_url,
            volume_label_hint,
        )
        if inferred_parent:
            if is_cached_book_url(inferred_parent):
                if aggregate_catalog:
                    aggregate_parent = inferred_parent
                    inferred_parent = None
                else:
                    details.entry_type = "volume"
                    details.original_post_url = inferred_parent
                    details.volumes = []
            else:
                orphaned_parent = inferred_parent

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    associated_names = encode_string_list(details.associated_names)
    genres = encode_string_list(details.genres)
    with connect_db() as connection:
        connection.execute(
            """
            UPDATE books
            SET entry_type = ?,
                original_post_url = ?,
                download_url = ?,
                detail_cached_at = ?,
                detail_attempts = 0,
                detail_last_attempt_at = ?,
                detail_last_error = NULL,
                associated_names = COALESCE(?, associated_names),
                genres = COALESCE(?, genres),
                synopsis = COALESCE(?, synopsis)
            WHERE url = ?
            """,
            (
                details.entry_type,
                details.original_post_url,
                details.download_url,
                now,
                now,
                associated_names,
                genres,
                details.synopsis,
                url,
            ),
        )
        if details.entry_type == "series":
            connection.execute(
                """
                UPDATE books
                SET associated_names = COALESCE(?, associated_names),
                    genres = COALESCE(?, genres),
                    synopsis = COALESCE(?, synopsis)
                WHERE original_post_url = ?
                """,
                (associated_names, genres, details.synopsis, url),
            )
    invalidate_book_filter_counts()
    if orphaned_parent:
        merge_orphaned_catalog(
            orphaned_parent,
            url,
            details.volumes,
        )
    elif inferred_parent:
        reparent_series_alias(url, inferred_parent)
    elif details.entry_type == "series":
        replace_series_volumes(
            url,
            apply_series_release_group_override(
                url,
                preserve_groups_for_partial_refresh(url, details.volumes),
            ),
        )
        build_fallback_catalog(url)
        mark_series_metadata_checked(url, now)
        reconcile_series_downloads(url)
        merge_cached_aggregate_catalogs_into_parent(url)
        if aggregate_parent:
            merge_aggregate_catalog_into_parent(aggregate_parent, url, details)
    elif details.original_post_url:
        build_fallback_catalog(details.original_post_url)
    if inferred_parent and details.parent_hints:
        merge_explicit_alias_catalogs(
            inferred_parent,
            details.parent_hints,
            url,
        )
    if details.entry_type == "series" and not inferred_parent and not aggregate_parent:
        merge_overlapping_catalog_aliases(url)
    catalog_url = details.original_post_url or (
        url if details.entry_type == "series" else None
    )
    if catalog_url:
        relabel_catalog_entries_from_books(catalog_url)
    for subseries_key, volumes in details.subseries_volumes.items():
        subseries_url = virtual_series_url(subseries_key)
        replace_series_volumes(subseries_url, volumes)
        reconcile_series_downloads(subseries_url)
        mark_series_metadata_checked(subseries_url, now)
    identity_key = ""
    with connect_db() as connection:
        refreshed_book = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (url,),
        ).fetchone()
    if refreshed_book:
        identity_key = series_identity_key(refreshed_book["title"], url)
    if identity_key in SHARED_INDEX_SUBSERIES or url in SHARED_INDEX_SUBSERIES.values():
        reconcile_shared_index_subseries()


def validate_post_details(details: PostDetails) -> None:
    can_recover_from_parent = (
        details.entry_type == "volume" and details.original_post_url is not None
    )
    if not details.download_url and not details.volumes and not can_recover_from_parent:
        raise ValueError("No download link found on the post.")


def replace_series_volumes(series_url: str, volumes: list[SeriesVolume]) -> None:
    series_catalog_repository.replace_series_volumes(
        connect_db,
        invalidate_catalog_indexes,
        series_url,
        volumes,
    )


def apply_series_release_group_override(
    series_url: str,
    volumes: list[SeriesVolume],
) -> list[SeriesVolume]:
    release_group = SERIES_RELEASE_GROUP_OVERRIDES.get(canonical_page_url(series_url))
    if not release_group:
        return volumes
    return [
        SeriesVolume(
            volume.label,
            volume.download_url,
            volume.position,
            volume.release_group or release_group,
            volume.source_list,
        )
        for volume in volumes
    ]


def preserve_groups_for_partial_refresh(
    series_url: str,
    refreshed_volumes: list[SeriesVolume],
) -> list[SeriesVolume]:
    if (
        canonical_page_url(series_url) not in PARTIAL_GROUP_PRESERVE_SERIES
        or not refreshed_volumes
        or len(refreshed_volumes) > 2
        or any(volume.release_group for volume in refreshed_volumes)
    ):
        return refreshed_volumes

    with connect_db() as connection:
        existing_rows = connection.execute(
            """
            SELECT label, download_url, release_group
            FROM series_volumes
            WHERE series_url = ?
              AND release_group IS NOT NULL
            ORDER BY position
            """,
            (series_url,),
        ).fetchall()
    groups_by_download = {
        row["download_url"]: row["release_group"] for row in existing_rows
    }
    refreshed_group = next(
        (
            groups_by_download.get(volume.download_url)
            for volume in refreshed_volumes
            if groups_by_download.get(volume.download_url)
        ),
        None,
    )
    if not refreshed_group:
        return refreshed_volumes

    preserved = [
        SeriesVolume(
            row["label"],
            row["download_url"],
            index,
            row["release_group"],
        )
        for index, row in enumerate(existing_rows)
        if row["release_group"] != refreshed_group
    ]
    preserved_downloads = {volume.download_url for volume in preserved}
    return preserved + [
        SeriesVolume(
            volume.label,
            volume.download_url,
            len(preserved) + index,
            refreshed_group,
        )
        for index, volume in enumerate(refreshed_volumes)
        if volume.download_url not in preserved_downloads
    ]


def merge_orphaned_catalog(
    orphaned_series_url: str,
    canonical_series_url: str,
    canonical_volumes: list[SeriesVolume],
) -> int:
    with connect_db() as connection:
        orphaned_rows = connection.execute(
            """
            SELECT label, download_url, position, release_group
            FROM series_volumes
            WHERE series_url = ?
            ORDER BY position
            """,
            (orphaned_series_url,),
        ).fetchall()

    group_override = MERGED_CATALOG_GROUP_OVERRIDES.get(
        canonical_page_url(canonical_series_url)
    )
    official = [
        SeriesVolume(
            row["label"],
            row["download_url"],
            index,
            row["release_group"] or (group_override[0] if group_override else None),
        )
        for index, row in enumerate(orphaned_rows)
    ]
    fan = [
        SeriesVolume(
            volume.label,
            volume.download_url,
            len(official) + index,
            volume.release_group or (group_override[1] if group_override else None),
        )
        for index, volume in enumerate(canonical_volumes)
        if re.match(r"^Volume\s+[0-9]", volume.label, re.IGNORECASE)
    ]
    merged = []
    seen_downloads: set[str] = set()
    for volume in official + fan:
        if volume.download_url in seen_downloads:
            continue
        seen_downloads.add(volume.download_url)
        merged.append(
            SeriesVolume(
                volume.label,
                volume.download_url,
                len(merged),
                volume.release_group,
            )
        )
    if not merged:
        return 0

    with connect_db() as connection:
        before = connection.total_changes
        connection.execute(
            """
            UPDATE books
            SET original_post_url = ?
            WHERE original_post_url = ?
            """,
            (canonical_series_url, orphaned_series_url),
        )
        connection.execute(
            "DELETE FROM series_volumes WHERE series_url = ?",
            (orphaned_series_url,),
        )
        connection.execute(
            "DELETE FROM series_status WHERE series_url = ?",
            (orphaned_series_url,),
        )
        changes = connection.total_changes - before
    replace_series_volumes(canonical_series_url, merged)
    return changes + len(merged)


def mark_series_metadata_checked(series_url: str, checked_at: str | None = None) -> None:
    cache_state.mark_series_metadata_checked(connect_db, series_url, checked_at)


def mark_series_unavailable(series_url: str) -> None:
    cache_state.mark_series_unavailable(connect_db, series_url)


def build_fallback_catalog(
    series_url: str,
    *,
    dedupe_labels: bool = False,
) -> int:
    return fallback_catalogs.build_fallback_catalog(
        series_url,
        connect_db=connect_db,
        is_aggregate_volume_title=is_aggregate_volume_title,
        resequence_series_volumes=resequence_series_volumes,
        invalidate_catalog_indexes=invalidate_catalog_indexes,
        dedupe_labels=dedupe_labels,
    )



def resequence_series_volumes(series_url: str) -> None:
    series_catalog_repository.resequence_series_volumes(connect_db, series_url)


def relabel_catalog_entries_from_books(series_url: str | None = None) -> int:
    return series_catalog_repository.relabel_catalog_entries_from_books(
        connect_db,
        is_aggregate_volume_title,
        invalidate_catalog_indexes,
        series_url,
    )


def build_missing_fallback_catalogs() -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT original_post_url
            FROM books
            WHERE entry_type = 'volume'
              AND original_post_url IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM series_volumes AS sv
                  WHERE sv.series_url = books.original_post_url
              )
            """
        ).fetchall()
    return sum(build_fallback_catalog(row["original_post_url"]) for row in rows)


def reparent_series_alias(
    old_series_url: str,
    new_series_url: str,
    *,
    dedupe_labels: bool = False,
) -> int:
    with connect_db() as connection:
        before = connection.total_changes
        connection.execute(
            """
            UPDATE books
            SET original_post_url = ?
            WHERE original_post_url = ?
            """,
            (new_series_url, old_series_url),
        )
        connection.execute(
            "DELETE FROM series_volumes WHERE series_url = ?",
            (old_series_url,),
        )
        connection.execute(
            "DELETE FROM series_status WHERE series_url = ?",
            (old_series_url,),
        )
        changes = connection.total_changes - before
    build_fallback_catalog(new_series_url, dedupe_labels=dedupe_labels)
    invalidate_catalog_indexes()
    return changes


def merge_overlapping_catalog_aliases(series_url: str | None = None) -> int:
    return catalog_aliases.merge_overlapping_catalog_aliases(
        series_url,
        connect_db=connect_db,
        is_aggregate_volume_title=is_aggregate_volume_title,
        delete_book_urls=delete_book_urls,
        invalidate_catalog_indexes=invalidate_catalog_indexes,
    )



def split_conflated_subseries_catalogs() -> int:
    return catalog_aliases.split_conflated_subseries_catalogs(
        connect_db=connect_db,
        replace_series_volumes=replace_series_volumes,
        build_fallback_catalog=build_fallback_catalog,
        invalidate_catalog_indexes=invalidate_catalog_indexes,
    )



def reconcile_aggregate_volume_catalogs() -> int:
    return aggregate_catalogs.reconcile_aggregate_volume_catalogs()


def reconcile_shared_index_subseries() -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT title, url, entry_type, original_post_url, download_url
            FROM books
            """
        ).fetchall()

    changes = 0
    for identity_key, index_url in SHARED_INDEX_SUBSERIES.items():
        family = [
            row
            for row in rows
            if series_identity_key(row["title"], row["url"]) == identity_key
        ]
        if not family:
            continue
        virtual_url = virtual_series_url(identity_key)
        old_catalogs = {
            row["url"]
            for row in family
            if row["entry_type"] == "series" and row["url"] != virtual_url
        }
        family_downloads = {
            row["download_url"] for row in family if row["download_url"]
        }
        with connect_db() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                UPDATE books
                SET entry_type = 'volume', original_post_url = ?
                WHERE url = ?
                """,
                [(virtual_url, row["url"]) for row in family],
            )
            connection.executemany(
                "DELETE FROM series_volumes WHERE series_url = ?",
                [(url,) for url in old_catalogs],
            )
            connection.executemany(
                "DELETE FROM series_status WHERE series_url = ?",
                [(url,) for url in old_catalogs],
            )
            connection.executemany(
                "DELETE FROM series_volumes WHERE series_url = ? AND download_url = ?",
                [(index_url, download_url) for download_url in family_downloads],
            )
            changes += connection.total_changes - before
        build_fallback_catalog(virtual_url)
        resequence_series_volumes(virtual_url)
        mark_series_metadata_checked(virtual_url)
    invalidate_catalog_indexes()
    return changes


def reconcile_catalog_relationships(*, force: bool = False) -> int:
    with connect_db() as connection:
        if not force and connection.execute(
            "SELECT 1 FROM metadata WHERE key = ?",
            (CATALOG_RECONCILE_KEY,),
        ).fetchone():
            return 0
    with connect_db() as connection:
        before = connection.total_changes
        connection.execute(
            """
            DELETE FROM series_volumes
            WHERE download_url IN (
                'http://mp4directs.com',
                'http://mp4directs.com/',
                'https://mp4directs.com',
                'https://mp4directs.com/'
            )
               OR (
                    lower(label) = 'complete edition'
                    AND (
                        lower(download_url) LIKE 'http://mp4directs.com/forums/%'
                        OR lower(download_url) LIKE 'https://mp4directs.com/forums/%'
                        OR lower(download_url) LIKE 'http://www.mp4directs.com/forums/%'
                        OR lower(download_url) LIKE 'https://www.mp4directs.com/forums/%'
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM series_volumes AS numbered
                        WHERE numbered.series_url = series_volumes.series_url
                          AND (
                              lower(numbered.label) LIKE 'volume %'
                              OR lower(numbered.label) LIKE 'part %'
                          )
                    )
               )
            """
        )
        changes = connection.total_changes - before
    changes += merge_overlapping_catalog_aliases()
    changes += reconcile_legacy_volume_entries(force=True)
    changes += split_conflated_subseries_catalogs()
    changes += reconcile_aggregate_volume_catalogs()
    changes += reconcile_shared_index_subseries()
    with connect_db() as connection:
        parent_urls = [
            row["original_post_url"]
            for row in connection.execute(
                """
                SELECT DISTINCT original_post_url
                FROM books
                WHERE entry_type = 'volume'
                  AND original_post_url IS NOT NULL
                """
            ).fetchall()
        ]
    changes += sum(build_fallback_catalog(url) for url in parent_urls)
    for parent_url in parent_urls:
        resequence_series_volumes(parent_url)
    changes += relabel_catalog_entries_from_books()
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                CATALOG_RECONCILE_KEY,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return changes


def cleanup_misclassified_series_tracking() -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT ss.series_url, b.original_post_url
            FROM series_status AS ss
            JOIN books AS b ON b.url = ss.series_url
            WHERE b.entry_type = 'volume'
              AND b.original_post_url IS NOT NULL
              AND b.original_post_url <> b.url
            """
        ).fetchall()
        external_parent_rows = connection.execute(
            """
            SELECT DISTINCT b.original_post_url, b.url, b.download_url
            FROM books AS b
            WHERE b.entry_type = 'volume'
              AND b.original_post_url IS NOT NULL
              AND lower(b.original_post_url) NOT LIKE 'https://jnovels.com/%'
              AND lower(b.original_post_url) NOT LIKE 'https://www.jnovels.com/%'
              AND lower(b.original_post_url) NOT LIKE 'virtual://jnovels/%'
            ORDER BY b.original_post_url, b.id
            """
        ).fetchall()

    changes = sum(
        reparent_series_alias(row["series_url"], row["original_post_url"])
        for row in rows
    )
    handled_external_parents: set[str] = set()
    for row in external_parent_rows:
        old_parent = row["original_post_url"]
        if old_parent in handled_external_parents:
            continue
        new_parent = infer_series_parent(
            row["url"],
            download_url=row["download_url"],
        )
        if not new_parent or not is_cached_series_url(new_parent):
            continue
        changes += reparent_series_alias(old_parent, new_parent)
        handled_external_parents.add(old_parent)

    excluded_urls = [
        urllib.parse.urljoin(BASE_URL, path)
        for path in EXCLUDED_SEARCH_PATHS
    ]
    with connect_db() as connection:
        before = connection.total_changes
        connection.executemany(
            """
            DELETE FROM series_status
            WHERE series_url = ?
              AND NOT EXISTS (
                  SELECT 1 FROM series_volumes
                  WHERE series_volumes.series_url = series_status.series_url
              )
              AND NOT EXISTS (
                  SELECT 1 FROM books
                  WHERE books.original_post_url = series_status.series_url
              )
            """,
            [(url,) for url in excluded_urls],
        )
        changes += connection.total_changes - before
    return changes


def reconcile_series_downloads(series_url: str) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        volume_rows = connection.execute(
            """
            SELECT label, download_url, release_group
            FROM series_volumes
            WHERE series_url = ?
            ORDER BY position
            """,
            (series_url,),
        ).fetchall()
        downloads_by_label: dict[tuple[str, str], str | None] = {}

        def record_download(label: str | None, format_key: str, download_url: str) -> None:
            if not label:
                return
            key = (label.casefold(), format_key)
            if key in downloads_by_label:
                downloads_by_label[key] = None
            else:
                downloads_by_label[key] = download_url

        for row in volume_rows:
            format_key = release_group_format_kind(row["release_group"]) or ""
            record_download(row["label"], format_key, row["download_url"])
            base_label = extract_catalog_label(row["label"])
            if base_label and base_label.casefold() != row["label"].casefold():
                record_download(base_label, format_key, row["download_url"])
        children = connection.execute(
            """
            SELECT url, title, download_url
            FROM books
            WHERE original_post_url = ?
            """,
            (series_url,),
        ).fetchall()
        updates = []
        for row in children:
            label = extract_catalog_label(row["title"])
            label_key = (label or "").casefold()
            child_format = book_page_format(row["title"], row["url"]) or ""
            download_url = (
                downloads_by_label.get((label_key, child_format))
                or downloads_by_label.get((label_key, ""))
            )
            if download_url and download_url != row["download_url"]:
                updates.append((download_url, now, now, row["url"]))
        connection.executemany(
            """
            UPDATE books
            SET download_url = ?,
                detail_cached_at = ?,
                detail_attempts = 0,
                detail_last_attempt_at = ?,
                detail_last_error = NULL
            WHERE url = ?
            """,
            updates,
        )
    if updates:
        invalidate_book_filter_counts()
    return len(updates)


def get_cached_urls() -> set[str]:
    return detail_queue.get_cached_urls(connect_db)


def record_detail_failure(url: str, error: Exception) -> None:
    detail_queue.record_detail_failure(connect_db, url, error)


def get_incomplete_detail_urls(
    limit: int,
    *,
    max_attempts: int | None = None,
    exclude_urls: set[str] | None = None,
) -> list[str]:
    return detail_queue.get_incomplete_detail_urls(
        connect_db,
        limit,
        max_attempts=max_attempts,
        exclude_urls=exclude_urls,
    )


def get_cache_health(
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, int]:
    return cache_state.get_cache_health(
        connect_db,
        auto_repair_max_attempts=AUTO_REPAIR_MAX_ATTEMPTS,
        stale_days=SERIES_STALE_DAYS,
        count_missing_series_index_entries=count_missing_series_index_entries,
        connection=connection,
    )


def series_stale_cutoff() -> str:
    return cache_state.series_stale_cutoff(SERIES_STALE_DAYS)


def stale_series_where_clause() -> str:
    return cache_state.stale_series_where_clause()


def count_stale_series(*, connection: sqlite3.Connection | None = None) -> int:
    return cache_state.count_stale_series(
        connect_db,
        SERIES_STALE_DAYS,
        connection=connection,
    )


def get_stale_series_urls(limit: int) -> list[str]:
    return cache_state.get_stale_series_urls(connect_db, SERIES_STALE_DAYS, limit)


def get_series_metadata_backfill_urls(
    limit: int,
    *,
    exclude_urls: set[str] | None = None,
) -> list[str]:
    return cache_state.get_series_metadata_backfill_urls(
        connect_db,
        limit,
        exclude_urls=exclude_urls,
    )


def get_series_refresh_urls(limit: int) -> list[str]:
    return cache_state.get_series_refresh_urls(connect_db, SERIES_STALE_DAYS, limit)


def list_repair_failures(limit: int = 100) -> list[dict[str, Any]]:
    return cache_state.list_repair_failures(connect_db, limit)


def is_cached_book_url(url: str) -> bool:
    return cache_state.is_cached_book_url(connect_db, url)


def is_established_series_book(url: str) -> bool:
    return cache_state.is_established_series_book(connect_db, url)


def is_cached_series_url(url: str) -> bool:
    return cache_state.is_cached_series_url(connect_db, url)


def set_series_preference(
    series_url: str,
    *,
    favorite: bool | None = None,
    hidden: bool | None = None,
) -> dict[str, Any]:
    preference = cache_state.set_series_preference(
        connect_db,
        series_url,
        favorite=favorite,
        hidden=hidden,
    )
    invalidate_book_filter_counts()
    return preference


def cache_content_signature() -> str:
    return cache_state.cache_content_signature(connect_db)


sync_lock = threading.Lock()
auto_sync_lock = threading.Lock()
last_auto_sync_started: float | None = None
sync_state: dict[str, Any] = {
    "running": False,
    "mode": None,
    "phase": None,
    "page": 0,
    "search_end_page": None,
    "pages_requested": 0,
    "pages_completed": 0,
    "items_seen": 0,
    "new_items": 0,
    "cache_changed": False,
    "details_completed": 0,
    "details_total": 0,
    "detail_errors": 0,
    "series_completed": 0,
    "series_total": 0,
    "series_errors": 0,
    "queued_details": 0,
    "queued_series": 0,
    "request_delay": None,
    "phase_started_at": None,
    "error": None,
    "notice": None,
    "started_at": None,
    "finished_at": None,
}


def set_sync_started(
    start_page: int,
    max_pages: int,
    delay: float,
    mode: str,
) -> None:
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sync_state.update(
        {
            "running": True,
            "mode": mode,
            "phase": "search",
            "page": start_page,
            "search_end_page": None,
            "pages_requested": max_pages,
            "pages_completed": 0,
            "items_seen": 0,
            "new_items": 0,
            "cache_changed": False,
            "details_completed": 0,
            "details_total": 0,
            "detail_errors": 0,
            "series_completed": 0,
            "series_total": 0,
            "series_errors": 0,
            "queued_details": 0,
            "queued_series": 0,
            "request_delay": delay,
            "phase_started_at": started_at,
            "error": None,
            "notice": None,
            "started_at": started_at,
            "finished_at": None,
        }
    )


def start_sync_thread(
    start_page: int,
    max_pages: int,
    delay: float,
    mode: str,
    detail_urls_override: list[str] | None = None,
    series_urls_override: list[str] | None = None,
    recovery: bool = False,
    *,
    name: str = "jnovels-sync",
) -> bool:
    if not sync_lock.acquire(blocking=False):
        return False

    set_sync_started(start_page, max_pages, delay, mode)
    thread = threading.Thread(
        target=run_sync,
        args=(
            start_page,
            max_pages,
            delay,
            mode,
            detail_urls_override,
            series_urls_override,
            recovery,
            True,
        ),
        name=name,
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        sync_state["running"] = False
        sync_lock.release()
        raise
    return True


def start_auto_catchup(
    max_pages: int = 20,
    delay: float = 1.0,
) -> tuple[bool, str | None, int]:
    global last_auto_sync_started
    with auto_sync_lock:
        now = time.monotonic()
        if last_auto_sync_started is not None:
            elapsed = now - last_auto_sync_started
            if elapsed < AUTO_SYNC_COOLDOWN_SECONDS:
                retry_after = max(int(AUTO_SYNC_COOLDOWN_SECONDS - elapsed + 0.999), 1)
                return False, "rate_limited", retry_after

        if not start_sync_thread(1, max_pages, delay, "catchup"):
            return False, "sync_running", 0

        last_auto_sync_started = now
        return True, None, 0


def collect_sync_books(
    mode: str,
    start_page: int,
    max_pages: int,
    delay: float,
    known_urls: set[str],
    on_page: Callable[[int, list[Book]], None] | None = None,
    cached_frontier: int = 0,
) -> list[Book]:
    return collect_sync_books_impl(
        mode,
        start_page,
        max_pages,
        delay,
        known_urls,
        sync_state=sync_state,
        fetch_page=fetch_page,
        sleep=time.sleep,
        on_page=on_page,
        cached_frontier=cached_frontier,
    )


def run_sync(
    start_page: int,
    max_pages: int,
    delay: float,
    mode: str = "manual",
    detail_urls_override: list[str] | None = None,
    series_urls_override: list[str] | None = None,
    recovery: bool = False,
    lock_acquired: bool = False,
) -> None:
    if not lock_acquired and not sync_lock.acquire(blocking=False):
        return

    initial_cache_signature: str | None = None
    completion_notice: str | None = None
    try:
        set_sync_started(start_page, max_pages, delay, mode)
        initial_cache_signature = cache_content_signature()
        checkpoint: dict[str, Any] = {
            "version": 1,
            "status": "active",
            "mode": mode,
            "start_page": start_page,
            "max_pages": max_pages,
            "delay": delay,
            "phase": "details" if detail_urls_override is not None else "search",
            "page": start_page,
            "pages_completed": 0,
            "pending_detail_urls": list(dict.fromkeys(detail_urls_override or [])),
            "pending_series_urls": list(dict.fromkeys(series_urls_override or [])),
            "started_at": sync_state["started_at"],
            "recovery": recovery,
        }
        save_sync_checkpoint(checkpoint)

        scheduled_series_urls: set[str] = set(series_urls_override or [])
        if detail_urls_override is not None:
            books = []
            detail_urls = list(dict.fromkeys(detail_urls_override))
        elif mode == "series_refresh":
            books = []
            detail_urls = []
            scheduled_series_urls.update(get_series_refresh_urls(max_pages))
        elif mode == "repair":
            books: list[Book] = []
            detail_urls = get_incomplete_detail_urls(max_pages)
        elif mode == "series_discovery":
            books = []
            sync_state["phase"] = "index"
            sync_state["phase_started_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            index_entries = fetch_series_index()
            index_total, index_new = store_series_index(index_entries)
            detail_urls = add_series_index_candidates(
                get_series_index_candidates(max_pages)
            )
            completion_notice = (
                f"Checked {index_total} indexed series"
                + (f"; {index_new} new to the index." if index_new else ".")
            )
        else:
            known_urls = get_cached_urls()
            scan_frontier = get_search_scan_frontier()
            applied_front_shift = 0

            def persist_page(page: int, page_books: list[Book]) -> None:
                nonlocal applied_front_shift, scan_frontier
                previous_orders = get_book_source_orders(
                    [book.url for book in page_books]
                )
                observed_shift = observed_source_page_shift(page, previous_orders)
                scan_frontier, applied_front_shift = apply_observed_front_shift(
                    scan_frontier,
                    applied_front_shift,
                    observed_shift,
                )
                upsert_books(page_books, source_page=page)
                if page_books and page <= scan_frontier + 1:
                    scan_frontier = max(scan_frontier, page)
                    set_metadata("search_scan_frontier", str(scan_frontier))
                cached_urls = get_cached_urls()
                page_books[:] = [
                    book for book in page_books if book.url in cached_urls
                ]
                pending = checkpoint["pending_detail_urls"]
                pending.extend(book.url for book in page_books if book.url not in pending)
                sync_state["queued_details"] = len(pending)
                checkpoint["page"] = page
                checkpoint["pages_completed"] = sync_state["pages_completed"]
                save_sync_checkpoint(checkpoint)

            books = collect_sync_books(
                mode,
                start_page,
                max_pages,
                delay,
                known_urls,
                persist_page,
                scan_frontier,
            )
            cached_urls = get_cached_urls()
            detail_urls = list(
                dict.fromkeys(
                    book.url for book in books if book.url in cached_urls
                )
            )
            detail_urls.extend(
                get_incomplete_detail_urls(
                    AUTO_REPAIR_LIMIT,
                    max_attempts=AUTO_REPAIR_MAX_ATTEMPTS,
                    exclude_urls=set(detail_urls),
                )
            )
            scheduled_series_urls.update(get_series_refresh_urls(AUTO_SERIES_REFRESH_LIMIT))
            if series_index_is_initialized():
                if series_index_refresh_due():
                    try:
                        store_series_index(fetch_series_index())
                    except (
                        urllib.error.URLError,
                        TimeoutError,
                        OSError,
                        ValueError,
                    ):
                        pass
                index_candidates = get_series_index_candidates(
                    AUTO_SERIES_INDEX_LIMIT
                )
                detail_urls.extend(add_series_index_candidates(index_candidates))
                detail_urls = list(dict.fromkeys(detail_urls))
        sync_state["phase"] = "details"
        sync_state["phase_started_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        sync_state["details_total"] = len(detail_urls)
        sync_state["queued_details"] = len(detail_urls)
        sync_state["queued_series"] = len(scheduled_series_urls)
        checkpoint["phase"] = "details"
        checkpoint["pending_detail_urls"] = list(dict.fromkeys(detail_urls))
        checkpoint["pending_series_urls"] = sorted(scheduled_series_urls)
        save_sync_checkpoint(checkpoint)
        discovered_series_urls: set[str] = set()
        detail_url_set = set(detail_urls)
        for index, url in enumerate(detail_urls):
            time.sleep(delay)
            completed = False
            try:
                details = fetch_post_details(url)
                validate_post_details(details)
                update_book_details(url, details)
                if details.entry_type == "series":
                    discovered_series_urls.add(url)
                elif details.original_post_url:
                    discovered_series_urls.add(details.original_post_url)
                completed = True
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
                sqlite3.Error,
            ) as exc:
                record_detail_failure(url, exc)
                sync_state["detail_errors"] += 1
                completed = True
            finally:
                if completed:
                    sync_state["details_completed"] += 1
                    sync_state["queued_details"] = len(detail_urls) - index - 1
                    sync_state["queued_series"] = len(
                        (scheduled_series_urls | discovered_series_urls)
                        - detail_url_set
                    )
                    checkpoint["pending_detail_urls"] = detail_urls[index + 1 :]
                    checkpoint["pending_series_urls"] = sorted(
                        scheduled_series_urls | discovered_series_urls
                    )
                    save_sync_checkpoint(checkpoint)

        missing_series_urls = sorted(
            (discovered_series_urls | scheduled_series_urls) - detail_url_set
        )
        sync_state["phase"] = "series"
        sync_state["phase_started_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        sync_state["series_total"] = len(missing_series_urls)
        sync_state["queued_series"] = len(missing_series_urls)
        checkpoint["phase"] = "series"
        checkpoint["pending_detail_urls"] = []
        checkpoint["pending_series_urls"] = missing_series_urls
        save_sync_checkpoint(checkpoint)
        for index, series_url in enumerate(missing_series_urls):
            time.sleep(delay)
            completed = False
            try:
                details = fetch_post_details(series_url)
                validate_post_details(details)
                update_book_details(series_url, details)
                completed = True
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
                sqlite3.Error,
            ) as exc:
                record_detail_failure(series_url, exc)
                sync_state["series_errors"] += 1
                completed = True
            finally:
                if completed:
                    sync_state["series_completed"] += 1
                    sync_state["queued_series"] = len(missing_series_urls) - index - 1
                    checkpoint["pending_series_urls"] = missing_series_urls[index + 1 :]
                    save_sync_checkpoint(checkpoint)

        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sync_state["cache_changed"] = (
            cache_content_signature() != initial_cache_signature
        )
        sync_state["finished_at"] = finished
        sync_state["phase"] = None
        sync_state["phase_started_at"] = None
        sync_state["queued_details"] = 0
        sync_state["queued_series"] = 0
        if recovery:
            sync_state["notice"] = (
                "Recovered saved work from an interrupted sync; "
                "the original search may have stopped early."
            )
            set_metadata("last_sync_recovered_at", finished)
        else:
            if sync_state["search_end_page"] is not None:
                sync_state["notice"] = (
                    "Reached the end of JNovels search results "
                    f"at page {sync_state['search_end_page']}."
                )
            elif completion_notice:
                sync_state["notice"] = completion_notice
            set_metadata("last_sync_at", finished)
            set_metadata("last_sync_pages", str(sync_state["pages_completed"]))
            set_metadata("last_detail_errors", str(sync_state["detail_errors"]))
            set_metadata("last_series_errors", str(sync_state["series_errors"]))
            delete_metadata("last_sync_interrupted_at")
        clear_sync_checkpoint()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        if initial_cache_signature is not None:
            sync_state["cache_changed"] = (
                cache_content_signature() != initial_cache_signature
            )
        sync_state["error"] = str(exc)
        sync_state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sync_state["phase"] = None
        sync_state["phase_started_at"] = None
    finally:
        sync_state["running"] = False
        sync_lock.release()


def recover_interrupted_sync(checkpoint: dict[str, Any] | None = None) -> bool:
    checkpoint = checkpoint or load_sync_checkpoint()
    if not checkpoint or checkpoint.get("status") != "active":
        return False

    interrupted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    set_metadata("last_sync_interrupted_at", interrupted_at)
    detail_urls = [
        str(url) for url in checkpoint.get("pending_detail_urls", []) if url
    ]
    series_urls = [
        str(url) for url in checkpoint.get("pending_series_urls", []) if url
    ]
    if not detail_urls and not series_urls:
        clear_sync_checkpoint()
        sync_state["notice"] = (
            "The previous sync stopped before more work was queued; "
            "completed search pages were saved."
        )
        return False

    run_sync(
        1,
        max(len(detail_urls), 1),
        min(max(float(checkpoint.get("delay", 1.0)), 0.5), 10.0),
        "recovery",
        detail_urls,
        series_urls,
        True,
    )
    return True


def start_interrupted_sync_recovery() -> bool:
    checkpoint = load_sync_checkpoint()
    if not checkpoint or checkpoint.get("status") != "active":
        return False
    thread = threading.Thread(
        target=recover_interrupted_sync,
        args=(checkpoint,),
        name="jnovels-sync-recovery",
        daemon=True,
    )
    thread.start()
    return True


def _configure_book_queries() -> None:
    _configure_aggregate_catalogs()
    book_queries.configure(
        db_path=DB_PATH,
        connect_db=connect_db,
        books_fts_available=books_fts_available,
        get_catalog_indexes=get_catalog_indexes,
        infer_aggregate_catalog_parents_from_db=infer_aggregate_catalog_parents_from_db,
        infer_aggregate_catalog_parent_from_db=infer_aggregate_catalog_parent_from_db,
        filter_pdf_rows_when_epub_exists=filter_pdf_rows_when_epub_exists,
    )


def _configure_aggregate_catalogs() -> None:
    aggregate_catalogs.configure(
        connect_db=connect_db,
        resequence_series_volumes=resequence_series_volumes,
        invalidate_catalog_indexes=invalidate_catalog_indexes,
    )


def parse_search_terms(query: str) -> list[str]:
    return book_queries.parse_search_terms(query)


def fts_query_for_term(term: str) -> str | None:
    return book_queries.fts_query_for_term(term)


def book_fts_filter(
    term: str,
    *,
    include_family_titles: bool,
) -> tuple[str, tuple[str, ...]] | None:
    _configure_book_queries()
    return book_queries.book_fts_filter(
        term,
        include_family_titles=include_family_titles,
    )


def book_filter(
    query: str,
    entry_filter: str = "all",
    exact_url: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    _configure_book_queries()
    return book_queries.book_filter(query, entry_filter, exact_url)


def count_books(
    query: str,
    entry_filter: str = "all",
    exact_url: str | None = None,
) -> int:
    _configure_book_queries()
    return book_queries.count_books(query, entry_filter, exact_url)


def count_book_filters() -> dict[str, int]:
    _configure_book_queries()
    return book_queries.count_book_filters()


def list_books(
    query: str,
    sort: str,
    limit: int | None = None,
    offset: int = 0,
    entry_filter: str = "all",
    exact_url: str | None = None,
) -> list[dict[str, Any]]:
    _configure_book_queries()
    return book_queries.list_books(
        query,
        sort,
        limit,
        offset,
        entry_filter,
        exact_url,
    )


def list_series_volumes(series_url: str) -> list[dict[str, Any]]:
    _configure_book_queries()
    return book_queries.list_series_volumes(series_url)


_configure_aggregate_catalogs()
AppHandler = create_app_handler(sys.modules[__name__])


def main() -> None:
    run_app(sys.modules[__name__])


if __name__ == "__main__":
    main()
