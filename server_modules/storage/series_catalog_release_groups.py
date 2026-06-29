from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from server_modules.domain.models import SeriesVolume
from server_modules.normalization.url_utils import canonical_page_url


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def apply_series_release_group_override(
    series_url: str,
    volumes: list[SeriesVolume],
    series_release_group_overrides: dict[str, str],
) -> list[SeriesVolume]:
    release_group = series_release_group_overrides.get(canonical_page_url(series_url))
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
    connect_db: ConnectDb,
    series_url: str,
    refreshed_volumes: list[SeriesVolume],
    partial_group_preserve_series: set[str],
) -> list[SeriesVolume]:
    if (
        canonical_page_url(series_url) not in partial_group_preserve_series
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
    connect_db: ConnectDb,
    replace_series_volumes: Callable[[str, list[SeriesVolume]], None],
    merged_catalog_group_overrides: dict[str, tuple[str, str]],
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

    group_override = merged_catalog_group_overrides.get(
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
