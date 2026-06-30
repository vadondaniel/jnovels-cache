from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.domain.models import PostDetails, SeriesVolume
from server_modules.normalization.label_utils import extract_catalog_label
from server_modules.storage import aggregate_catalog_inference


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def merge_aggregate_catalog_into_parent(
    parent_url: str,
    aggregate_url: str,
    details: PostDetails,
    *,
    connect_db: ConnectDb,
    resequence_series_volumes: Callable[[str], None],
    invalidate_catalog_indexes: Callable[[], None],
) -> int:
    source_volumes = [
        volume
        for volume in details.volumes
        if re.match(r"^Volume\s+[0-9]", volume.label, re.IGNORECASE)
    ]
    if len(source_volumes) < 2:
        return 0
    with connect_db() as connection:
        book = connection.execute(
            "SELECT title, download_url FROM books WHERE url = ?",
            (aggregate_url,),
        ).fetchone()
        if not book:
            return 0
        group = aggregate_catalog_inference.aggregate_release_group(
            book["title"],
            details,
        )
        aggregate_label = extract_catalog_label(book["title"])
        parent_rows = connection.execute(
            """
            SELECT label, download_url, release_group
            FROM series_volumes
            WHERE series_url = ?
            """,
            (parent_url,),
        ).fetchall()
        existing_urls_by_label: dict[str, set[str]] = {}
        for row in parent_rows:
            if (row["release_group"] or "").casefold() == group.casefold():
                continue
            existing_urls_by_label.setdefault(row["label"].casefold(), set()).add(
                row["download_url"]
            )
        merged_volumes = [
            volume
            for volume in source_volumes
            if volume.download_url
            not in existing_urls_by_label.get(volume.label.casefold(), set())
        ]
        before = connection.total_changes
        connection.execute(
            "DELETE FROM series_volumes WHERE series_url = ? AND release_group = ?",
            (parent_url, group),
        )
        if aggregate_label:
            connection.execute(
                """
                DELETE FROM series_volumes
                WHERE series_url = ?
                  AND lower(label) = lower(?)
                  AND download_url = ?
                """,
                (parent_url, aggregate_label, book["download_url"]),
            )
        if merged_volumes:
            next_position = connection.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM series_volumes
                WHERE series_url = ?
                """,
                (parent_url,),
            ).fetchone()[0]
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            connection.executemany(
                """
                INSERT OR IGNORE INTO series_volumes
                    (series_url, label, download_url, position, cached_at, release_group)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        parent_url,
                        volume.label,
                        volume.download_url,
                        next_position + index,
                        now,
                        group,
                    )
                    for index, volume in enumerate(merged_volumes)
                ],
            )
        changes = connection.total_changes - before
    if changes:
        resequence_series_volumes(parent_url)
        invalidate_catalog_indexes()
    return changes


def merge_cached_aggregate_catalogs_into_parent(
    parent_url: str,
    *,
    connect_db: ConnectDb,
    resequence_series_volumes: Callable[[str], None],
    invalidate_catalog_indexes: Callable[[], None],
) -> int:
    with connect_db() as connection:
        books = connection.execute(
            """
            SELECT title, url, download_url
            FROM books
            WHERE entry_type = 'series'
              AND url <> ?
            ORDER BY published_at IS NULL, published_at, id
            """,
            (parent_url,),
        ).fetchall()
        candidates = [
            book
            for book in books
            if aggregate_catalog_inference.is_aggregate_volume_title(
                book["title"],
                book["url"],
            )
            and aggregate_catalog_inference.infer_aggregate_catalog_parent_from_db(
                book["url"],
                book["title"],
                connect_db=connect_db,
            )
            == parent_url
        ]
        volume_rows = {
            book["url"]: connection.execute(
                """
                SELECT label, download_url, position, release_group
                FROM series_volumes
                WHERE series_url = ?
                ORDER BY position
                """,
                (book["url"],),
            ).fetchall()
            for book in candidates
        }
    changes = 0
    for book in candidates:
        rows = volume_rows.get(book["url"], [])
        if len(rows) < 2:
            continue
        details = PostDetails(
            "series",
            None,
            book["download_url"],
            [
                SeriesVolume(
                    row["label"],
                    row["download_url"],
                    row["position"],
                    row["release_group"],
                )
                for row in rows
            ],
        )
        changes += merge_aggregate_catalog_into_parent(
            parent_url,
            book["url"],
            details,
            connect_db=connect_db,
            resequence_series_volumes=resequence_series_volumes,
            invalidate_catalog_indexes=invalidate_catalog_indexes,
        )
    return changes
