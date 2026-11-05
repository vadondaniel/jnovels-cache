from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone

from server_modules.domain.models import PostDetails, SeriesVolume
from server_modules.normalization.text_utils import encode_string_list
from server_modules.normalization.title_utils import (
    series_identity_key,
    virtual_series_url,
)


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


@dataclass(frozen=True)
class DetailUpdateDeps:
    connect_db: ConnectDb
    is_aggregate_volume_catalog: Callable[[str, PostDetails], bool]
    is_established_series_book: Callable[[str], bool]
    infer_aggregate_catalog_parent: Callable[[str, PostDetails], str | None]
    infer_series_parent: Callable[..., str | None]
    is_cached_book_url: Callable[[str], bool]
    invalidate_book_filter_counts: Callable[[], None]
    merge_orphaned_catalog: Callable[[str, str, list[SeriesVolume]], int]
    reparent_series_alias: Callable[[str, str], int]
    replace_series_volumes: Callable[[str, list[SeriesVolume]], None]
    apply_series_release_group_override: Callable[
        [str, list[SeriesVolume]],
        list[SeriesVolume],
    ]
    preserve_groups_for_partial_refresh: Callable[
        [str, list[SeriesVolume]],
        list[SeriesVolume],
    ]
    build_fallback_catalog: Callable[[str], int]
    mark_series_metadata_checked: Callable[[str, str | None], None]
    reconcile_series_downloads: Callable[[str], int]
    merge_cached_aggregate_catalogs_into_parent: Callable[[str], int]
    merge_aggregate_catalog_into_parent: Callable[[str, str, PostDetails], int]
    merge_explicit_alias_catalogs: Callable[[str, list[str], str], int]
    merge_overlapping_catalog_aliases: Callable[[str | None], int]
    relabel_catalog_entries_from_books: Callable[[str | None], int]
    reconcile_shared_index_subseries: Callable[[], int]
    shared_index_subseries: dict[str, str]


def update_book_details(url: str, details: PostDetails, deps: DetailUpdateDeps) -> None:
    inferred_parent: str | None = None
    orphaned_parent: str | None = None
    aggregate_parent: str | None = None
    aggregate_catalog = (
        details.entry_type == "series"
        and not details.original_post_url
        and deps.is_aggregate_volume_catalog(url, details)
    )
    if (
        details.entry_type == "series"
        and not details.original_post_url
        and (aggregate_catalog or not deps.is_established_series_book(url))
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
            deps.infer_aggregate_catalog_parent(url, details)
            if aggregate_catalog
            else None
        ) or deps.infer_series_parent(
            url,
            [*details.parent_hints, *details.associated_names],
            details.download_url,
            volume_label_hint,
        )
        if inferred_parent:
            if deps.is_cached_book_url(inferred_parent):
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
    with deps.connect_db() as connection:
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
    deps.invalidate_book_filter_counts()
    if orphaned_parent:
        deps.merge_orphaned_catalog(
            orphaned_parent,
            url,
            details.volumes,
        )
    elif inferred_parent:
        deps.reparent_series_alias(url, inferred_parent)
    elif details.entry_type == "series":
        deps.replace_series_volumes(
            url,
            deps.apply_series_release_group_override(
                url,
                deps.preserve_groups_for_partial_refresh(url, details.volumes),
            ),
        )
        deps.build_fallback_catalog(url)
        deps.mark_series_metadata_checked(url, now)
        deps.reconcile_series_downloads(url)
        deps.merge_cached_aggregate_catalogs_into_parent(url)
        if aggregate_parent:
            deps.merge_aggregate_catalog_into_parent(aggregate_parent, url, details)
    elif details.original_post_url:
        deps.build_fallback_catalog(details.original_post_url)
    if inferred_parent and details.parent_hints:
        deps.merge_explicit_alias_catalogs(
            inferred_parent,
            details.parent_hints,
            url,
        )
    if details.entry_type == "series" and not inferred_parent and not aggregate_parent:
        deps.merge_overlapping_catalog_aliases(url)
    catalog_url = details.original_post_url or (
        url if details.entry_type == "series" else None
    )
    if catalog_url:
        deps.relabel_catalog_entries_from_books(catalog_url)
    for subseries_key, volumes in details.subseries_volumes.items():
        subseries_url = virtual_series_url(subseries_key)
        deps.replace_series_volumes(subseries_url, volumes)
        deps.reconcile_series_downloads(subseries_url)
        deps.mark_series_metadata_checked(subseries_url, now)
    identity_key = ""
    with deps.connect_db() as connection:
        refreshed_book = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (url,),
        ).fetchone()
    if refreshed_book:
        identity_key = series_identity_key(refreshed_book["title"], url)
    if identity_key in deps.shared_index_subseries or url in deps.shared_index_subseries.values():
        deps.reconcile_shared_index_subseries()
    if details.entry_type == "series" and not inferred_parent:
        with deps.connect_db() as connection:
            connection.execute(
                "UPDATE series_status SET checked_at = ?, "
                "source_change_pending = CASE WHEN source_modified_at > ? THEN 1 ELSE 0 END "
                "WHERE series_url = ?", (now, now, url),
            )


def validate_post_details(details: PostDetails) -> None:
    can_recover_from_parent = (
        details.entry_type == "volume" and details.original_post_url is not None
    )
    if not details.download_url and not details.volumes and not can_recover_from_parent:
        raise ValueError("No download link found on the post.")
