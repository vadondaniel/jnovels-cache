from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.domain.models import SeriesVolume
from server_modules.normalization.label_utils import extract_catalog_label


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def build_fallback_catalog(
    series_url: str,
    *,
    connect_db: ConnectDb,
    is_aggregate_volume_title: Callable[[str, str], bool],
    resequence_series_volumes: Callable[[str], None],
    invalidate_catalog_indexes: Callable[[], None],
    dedupe_labels: bool = False,
) -> int:
    with connect_db() as connection:
        existing_rows = connection.execute(
            """
            SELECT label, download_url, position, release_group
            FROM series_volumes
            WHERE series_url = ?
            ORDER BY position
            """,
            (series_url,),
        ).fetchall()
        rows = connection.execute(
            """
            SELECT title, url, download_url
            FROM books
            WHERE original_post_url = ?
              AND download_url IS NOT NULL
            ORDER BY published_at, id
            """,
            (series_url,),
        ).fetchall()

    child_releases = [
        (
            extract_catalog_label(row["title"]),
            row["download_url"],
        )
        for row in rows
        if not is_aggregate_volume_title(row["title"], row["url"])
    ]
    existing_labels = {row["label"].casefold() for row in existing_rows}
    existing_downloads = {row["download_url"] for row in existing_rows}
    next_position = max((row["position"] for row in existing_rows), default=-1) + 1
    volumes: list[SeriesVolume] = []
    for label, download_url in child_releases:
        label_key = (label or "").casefold()
        base_volume = re.match(r"^(volume\s+[0-9]+(?:\.[0-9]+)?)\s+", label_key)
        if (
            not label
            or label_key in existing_labels
            or (base_volume and base_volume.group(1) in existing_labels)
            or download_url in existing_downloads
        ):
            continue
        existing_labels.add(label_key)
        existing_downloads.add(download_url)
        volumes.append(
            SeriesVolume(
                label,
                download_url,
                next_position + len(volumes),
                None,
            )
        )
    if not volumes:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO series_volumes
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
                for volume in volumes
            ],
        )
        connection.execute(
            """
            INSERT INTO series_status (series_url, checked_at)
            VALUES (?, ?)
            ON CONFLICT(series_url) DO NOTHING
            """,
            (series_url, now),
        )
    resequence_series_volumes(series_url)
    invalidate_catalog_indexes()
    return len(volumes)

