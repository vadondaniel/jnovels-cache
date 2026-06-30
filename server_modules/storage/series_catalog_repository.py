from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.domain.models import SeriesVolume
from server_modules.normalization.label_utils import (
    extract_catalog_label,
)
from server_modules.storage import series_download_reconciliation
from server_modules.storage import series_catalog_release_groups
from server_modules.storage import series_volume_resequencing


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def replace_series_volumes(
    connect_db: ConnectDb,
    invalidate_catalog_indexes: Callable[[], None],
    series_url: str,
    volumes: list[SeriesVolume],
) -> None:
    normalized_volumes: list[SeriesVolume] = []
    seen_downloads: set[str] = set()
    for volume in volumes:
        if volume.download_url in seen_downloads:
            continue
        seen_downloads.add(volume.download_url)
        normalized_volumes.append(
            SeriesVolume(
                volume.label,
                volume.download_url,
                len(normalized_volumes),
                volume.release_group,
            )
        )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            "DELETE FROM series_volumes WHERE series_url = ?",
            (series_url,),
        )
        connection.executemany(
            """
            INSERT INTO series_volumes
                (series_url, label, download_url, position, cached_at, release_group)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    series_url,
                    volume.label,
                    volume.download_url,
                    volume.position,
                    now,
                    volume.release_group,
                )
                for volume in normalized_volumes
            ],
        )
        connection.execute(
            """
            INSERT INTO series_status (series_url, checked_at)
            VALUES (?, ?)
            ON CONFLICT(series_url) DO UPDATE
            SET checked_at = excluded.checked_at,
                unavailable_at = NULL
            """,
            (series_url, now),
        )
    invalidate_catalog_indexes()


def apply_series_release_group_override(
    series_url: str,
    volumes: list[SeriesVolume],
    series_release_group_overrides: dict[str, str],
) -> list[SeriesVolume]:
    return series_catalog_release_groups.apply_series_release_group_override(
        series_url,
        volumes,
        series_release_group_overrides,
    )


def preserve_groups_for_partial_refresh(
    connect_db: ConnectDb,
    series_url: str,
    refreshed_volumes: list[SeriesVolume],
    partial_group_preserve_series: set[str],
) -> list[SeriesVolume]:
    return series_catalog_release_groups.preserve_groups_for_partial_refresh(
        connect_db,
        series_url,
        refreshed_volumes,
        partial_group_preserve_series,
    )


def merge_orphaned_catalog(
    connect_db: ConnectDb,
    replace_series_volumes: Callable[[str, list[SeriesVolume]], None],
    merged_catalog_group_overrides: dict[str, tuple[str, str]],
    orphaned_series_url: str,
    canonical_series_url: str,
    canonical_volumes: list[SeriesVolume],
) -> int:
    return series_catalog_release_groups.merge_orphaned_catalog(
        connect_db,
        replace_series_volumes,
        merged_catalog_group_overrides,
        orphaned_series_url,
        canonical_series_url,
        canonical_volumes,
    )


def resequence_series_volumes(
    connect_db: ConnectDb,
    series_url: str,
) -> None:
    series_volume_resequencing.resequence_series_volumes(connect_db, series_url)


def relabel_catalog_entries_from_books(
    connect_db: ConnectDb,
    is_aggregate_volume_title: Callable[[str, str], bool],
    invalidate_catalog_indexes: Callable[[], None],
    series_url: str | None = None,
) -> int:
    where_clause = "WHERE sv.series_url = ?" if series_url else ""
    params: tuple[str, ...] = (series_url,) if series_url else ()
    with connect_db() as connection:
        rows = connection.execute(
            f"""
            SELECT sv.id, sv.label, sv.series_url,
                   child.title AS child_title, child.url AS child_url
            FROM series_volumes AS sv
            JOIN books AS child ON child.download_url = sv.download_url
                AND (
                    child.url = sv.series_url
                    OR child.original_post_url = sv.series_url
                )
            {where_clause}
            ORDER BY sv.id,
                     child.url = sv.series_url DESC,
                     child.published_at DESC,
                     child.id DESC
            """,
            params,
        ).fetchall()
        corrections = []
        affected_series: set[str] = set()
        seen_ids: set[int] = set()
        for row in rows:
            if row["id"] in seen_ids:
                continue
            seen_ids.add(row["id"])
            if is_aggregate_volume_title(row["child_title"], row["child_url"]):
                continue
            label = extract_catalog_label(row["child_title"])
            current_label = extract_catalog_label(row["label"]) or row["label"]
            if label and label.casefold() != current_label.casefold():
                corrections.append((label, row["id"]))
                affected_series.add(row["series_url"])
        if corrections:
            connection.executemany(
                "UPDATE series_volumes SET label = ? WHERE id = ?",
                corrections,
            )
    if corrections:
        for affected_url in affected_series:
            resequence_series_volumes(connect_db, affected_url)
        invalidate_catalog_indexes()
    return len(corrections)


def build_missing_fallback_catalogs(
    connect_db: ConnectDb,
    build_fallback_catalog: Callable[[str], int],
) -> int:
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
    connect_db: ConnectDb,
    build_fallback_catalog: Callable[..., int],
    invalidate_catalog_indexes: Callable[[], None],
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


def reconcile_series_downloads(
    connect_db: ConnectDb,
    invalidate_book_filter_counts: Callable[[], None],
    series_url: str,
) -> int:
    return series_download_reconciliation.reconcile_series_downloads(
        connect_db,
        invalidate_book_filter_counts,
        series_url,
    )
