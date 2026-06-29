from __future__ import annotations

import sqlite3
import sys
import threading
import time
import urllib.error
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
from server_modules.storage import catalog_relationship_cleanup
from server_modules.storage import cache_state
from server_modules.storage import detail_queue
from server_modules.storage import detail_updates
from server_modules.storage import fallback_catalogs
from server_modules.storage import legacy_volume_cleanup
from server_modules.storage import metadata_store
from server_modules.storage import series_parent_inference
from server_modules.storage import series_index_store
from server_modules.storage import series_catalog_repository
from server_modules.sync.collection import collect_sync_books as collect_sync_books_impl
from server_modules.sync.runner import (
    SyncRunnerConfig,
    SyncRunnerDeps,
    run_sync_worker,
)
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
    release_group_from_catalog_text,
    release_group_from_heading,
)
from server_modules.parsers.search_results import SearchResultsParser, normalize_date
from server_modules.parsers.series_index import SeriesIndexParser
from server_modules.normalization.title_utils import (
    book_page_format as parse_book_page_format,
    is_distinct_subseries_title,
    is_named_subseries_title,
    is_numbered_subseries_title,
    normalize_display_title,
    parse_book_identity,
    qualify_numbered_subseries_badge,
    series_identity_key as parse_series_identity_key,
)
from server_modules.normalization.text_utils import (
    decode_string_list,
    normalize_legacy_title_case,
    normalize_synopsis,
    unique_nonempty_lines,
)
from server_modules.normalization.url_utils import (
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
    "parse_book_identity",
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
_URLError = urllib.error.URLError


def parse_positive_int(value: str, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def book_page_format(title: str, url: str) -> str | None:
    return parse_book_page_format(title, url)


def series_identity_key(title: str, url: str) -> str:
    return parse_series_identity_key(title, url)



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
    return legacy_volume_cleanup.reconcile_legacy_volume_entries(
        force=force,
        legacy_volume_reconcile_key=LEGACY_VOLUME_RECONCILE_KEY,
        connect_db=connect_db,
        is_aggregate_volume_title=is_aggregate_volume_title,
        infer_series_parent=infer_series_parent,
        is_cached_series_url=is_cached_series_url,
        reparent_series_alias=reparent_series_alias,
        build_fallback_catalog=build_fallback_catalog,
    )


def group_orphan_volume_families() -> int:
    return legacy_volume_cleanup.group_orphan_volume_families(
        connect_db=connect_db,
        is_aggregate_volume_title=is_aggregate_volume_title,
        reparent_series_alias=reparent_series_alias,
        build_fallback_catalog=build_fallback_catalog,
    )


def update_book_details(url: str, details: PostDetails) -> None:
    detail_updates.update_book_details(
        url,
        details,
        detail_updates.DetailUpdateDeps(
            connect_db=connect_db,
            is_aggregate_volume_catalog=is_aggregate_volume_catalog,
            is_established_series_book=is_established_series_book,
            infer_aggregate_catalog_parent=infer_aggregate_catalog_parent,
            infer_series_parent=infer_series_parent,
            is_cached_book_url=is_cached_book_url,
            invalidate_book_filter_counts=invalidate_book_filter_counts,
            merge_orphaned_catalog=merge_orphaned_catalog,
            reparent_series_alias=reparent_series_alias,
            replace_series_volumes=replace_series_volumes,
            apply_series_release_group_override=apply_series_release_group_override,
            preserve_groups_for_partial_refresh=preserve_groups_for_partial_refresh,
            build_fallback_catalog=build_fallback_catalog,
            mark_series_metadata_checked=mark_series_metadata_checked,
            reconcile_series_downloads=reconcile_series_downloads,
            merge_cached_aggregate_catalogs_into_parent=merge_cached_aggregate_catalogs_into_parent,
            merge_aggregate_catalog_into_parent=merge_aggregate_catalog_into_parent,
            merge_explicit_alias_catalogs=merge_explicit_alias_catalogs,
            merge_overlapping_catalog_aliases=merge_overlapping_catalog_aliases,
            relabel_catalog_entries_from_books=relabel_catalog_entries_from_books,
            reconcile_shared_index_subseries=reconcile_shared_index_subseries,
            shared_index_subseries=SHARED_INDEX_SUBSERIES,
        ),
    )


def validate_post_details(details: PostDetails) -> None:
    detail_updates.validate_post_details(details)


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
    return series_catalog_repository.apply_series_release_group_override(
        series_url,
        volumes,
        SERIES_RELEASE_GROUP_OVERRIDES,
    )


def preserve_groups_for_partial_refresh(
    series_url: str,
    refreshed_volumes: list[SeriesVolume],
) -> list[SeriesVolume]:
    return series_catalog_repository.preserve_groups_for_partial_refresh(
        connect_db,
        series_url,
        refreshed_volumes,
        PARTIAL_GROUP_PRESERVE_SERIES,
    )


def merge_orphaned_catalog(
    orphaned_series_url: str,
    canonical_series_url: str,
    canonical_volumes: list[SeriesVolume],
) -> int:
    return series_catalog_repository.merge_orphaned_catalog(
        connect_db,
        replace_series_volumes,
        MERGED_CATALOG_GROUP_OVERRIDES,
        orphaned_series_url,
        canonical_series_url,
        canonical_volumes,
    )


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
    return series_catalog_repository.build_missing_fallback_catalogs(
        connect_db,
        build_fallback_catalog,
    )


def reparent_series_alias(
    old_series_url: str,
    new_series_url: str,
    *,
    dedupe_labels: bool = False,
) -> int:
    return series_catalog_repository.reparent_series_alias(
        connect_db,
        build_fallback_catalog,
        invalidate_catalog_indexes,
        old_series_url,
        new_series_url,
        dedupe_labels=dedupe_labels,
    )


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
    return catalog_relationship_cleanup.reconcile_shared_index_subseries(
        shared_index_subseries=SHARED_INDEX_SUBSERIES,
        connect_db=connect_db,
        build_fallback_catalog=build_fallback_catalog,
        resequence_series_volumes=resequence_series_volumes,
        mark_series_metadata_checked=mark_series_metadata_checked,
        invalidate_catalog_indexes=invalidate_catalog_indexes,
    )


def reconcile_catalog_relationships(*, force: bool = False) -> int:
    return catalog_relationship_cleanup.reconcile_catalog_relationships(
        force=force,
        catalog_reconcile_key=CATALOG_RECONCILE_KEY,
        shared_index_subseries=SHARED_INDEX_SUBSERIES,
        connect_db=connect_db,
        merge_overlapping_catalog_aliases=merge_overlapping_catalog_aliases,
        reconcile_legacy_volume_entries=reconcile_legacy_volume_entries,
        split_conflated_subseries_catalogs=split_conflated_subseries_catalogs,
        reconcile_aggregate_volume_catalogs=reconcile_aggregate_volume_catalogs,
        build_fallback_catalog=build_fallback_catalog,
        resequence_series_volumes=resequence_series_volumes,
        relabel_catalog_entries_from_books=relabel_catalog_entries_from_books,
        mark_series_metadata_checked=mark_series_metadata_checked,
        invalidate_catalog_indexes=invalidate_catalog_indexes,
    )


def cleanup_misclassified_series_tracking() -> int:
    return catalog_relationship_cleanup.cleanup_misclassified_series_tracking(
        base_url=BASE_URL,
        excluded_search_paths=EXCLUDED_SEARCH_PATHS,
        connect_db=connect_db,
        reparent_series_alias=reparent_series_alias,
        infer_series_parent=infer_series_parent,
        is_cached_series_url=is_cached_series_url,
    )


def reconcile_series_downloads(series_url: str) -> int:
    return series_catalog_repository.reconcile_series_downloads(
        connect_db,
        invalidate_book_filter_counts,
        series_url,
    )


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

    try:
        run_sync_worker(
            start_page,
            max_pages,
            delay,
            mode,
            detail_urls_override,
            series_urls_override,
            recovery,
            config=SyncRunnerConfig(
                auto_repair_limit=AUTO_REPAIR_LIMIT,
                auto_repair_max_attempts=AUTO_REPAIR_MAX_ATTEMPTS,
                auto_series_index_limit=AUTO_SERIES_INDEX_LIMIT,
                auto_series_refresh_limit=AUTO_SERIES_REFRESH_LIMIT,
            ),
            deps=SyncRunnerDeps(
                sync_state=sync_state,
                set_sync_started=set_sync_started,
                cache_content_signature=cache_content_signature,
                save_sync_checkpoint=save_sync_checkpoint,
                clear_sync_checkpoint=clear_sync_checkpoint,
                get_series_refresh_urls=get_series_refresh_urls,
                get_incomplete_detail_urls=get_incomplete_detail_urls,
                fetch_series_index=fetch_series_index,
                store_series_index=store_series_index,
                add_series_index_candidates=add_series_index_candidates,
                get_series_index_candidates=get_series_index_candidates,
                get_cached_urls=get_cached_urls,
                get_search_scan_frontier=get_search_scan_frontier,
                get_book_source_orders=get_book_source_orders,
                observed_source_page_shift=observed_source_page_shift,
                apply_observed_front_shift=apply_observed_front_shift,
                upsert_books=upsert_books,
                set_metadata=set_metadata,
                delete_metadata=delete_metadata,
                collect_sync_books=collect_sync_books,
                series_index_is_initialized=series_index_is_initialized,
                series_index_refresh_due=series_index_refresh_due,
                fetch_post_details=fetch_post_details,
                validate_post_details=validate_post_details,
                update_book_details=update_book_details,
                record_detail_failure=record_detail_failure,
                sleep=time.sleep,
            ),
        )
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
