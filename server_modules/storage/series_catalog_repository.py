from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.domain.models import SeriesVolume
from server_modules.normalization.label_utils import (
    extract_catalog_label,
    normalize_volume_number,
)
from server_modules.normalization.release_groups import release_group_format_kind
from server_modules.normalization.title_utils import book_page_format
from server_modules.storage import series_catalog_release_groups


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
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT sv.id, sv.label, sv.position, sv.release_group,
                   EXISTS (
                       SELECT 1 FROM books AS child
                       WHERE child.download_url = sv.download_url
                         AND (child.url = sv.series_url OR child.original_post_url = sv.series_url)
                   ) AS has_post
            FROM series_volumes AS sv
            WHERE sv.series_url = ?
            ORDER BY position, id
            """,
            (series_url,),
        ).fetchall()
        retained: dict[tuple[str, str], sqlite3.Row] = {}
        normalized_labels: dict[int, str] = {}
        discarded: set[int] = set()
        for row in rows:
            legacy = re.search(r"\s([0-9]+(?:\.[0-9]+)?)_\s*\S", row["label"])
            label = (
                f"Volume {normalize_volume_number(legacy.group(1))}"
                if legacy
                else row["label"]
            )
            normalized_labels[row["id"]] = label
            key = ((row["release_group"] or "").casefold(), label.casefold())
            existing = retained.get(key)
            if existing is None or row["has_post"] > existing["has_post"]:
                if existing is not None:
                    discarded.add(existing["id"])
                retained[key] = row
            else:
                discarded.add(row["id"])
        if discarded:
            connection.executemany(
                "DELETE FROM series_volumes WHERE id = ?",
                [(row_id,) for row_id in discarded],
            )
        connection.executemany(
            "UPDATE series_volumes SET label = ? WHERE id = ?",
            [
                (normalized_labels[row["id"]], row["id"])
                for row in retained.values()
                if normalized_labels[row["id"]] != row["label"]
            ],
        )
        rows = list(retained.values())
        group_order: dict[str, int] = {}
        for row in rows:
            group_key = (row["release_group"] or "").casefold()
            group_order.setdefault(group_key, len(group_order))

        def volume_order(row: sqlite3.Row) -> tuple[int, int, int, float, int]:
            group_key = (row["release_group"] or "").casefold()
            label = normalized_labels[row["id"]]
            year = re.search(r"\bYear\s+([0-9]+)\b", label, re.IGNORECASE)
            volume_match = re.search(
                r"\b(?:Volume|Vol\.)\s+([0-9]+(?:\.[0-9]+)?)",
                label,
                re.IGNORECASE,
            )
            part_match = re.search(
                r"\bPart\s+([0-9]+(?:\.[0-9]+)?)",
                label,
                re.IGNORECASE,
            )
            number = volume_match or part_match
            label_kind = 0 if volume_match else 2 if part_match else 1
            return (
                group_order[group_key],
                int(year.group(1)) if year else 1,
                label_kind,
                float(number.group(1)) if number else float("inf"),
                row["position"],
            )

        ordered = sorted(rows, key=volume_order)
        connection.executemany(
            "UPDATE series_volumes SET position = ? WHERE id = ?",
            [(position, row["id"]) for position, row in enumerate(ordered)],
        )


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
