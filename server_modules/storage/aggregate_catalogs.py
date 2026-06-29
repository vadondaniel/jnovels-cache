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

_connect_db: ConnectDb | None = None
_resequence_series_volumes: Callable[[str], None] | None = None
_invalidate_catalog_indexes: Callable[[], None] | None = None


def configure(
    *,
    connect_db: ConnectDb,
    resequence_series_volumes: Callable[[str], None],
    invalidate_catalog_indexes: Callable[[], None],
) -> None:
    global _connect_db
    global _resequence_series_volumes
    global _invalidate_catalog_indexes
    _connect_db = connect_db
    _resequence_series_volumes = resequence_series_volumes
    _invalidate_catalog_indexes = invalidate_catalog_indexes


def connect_db() -> AbstractContextManager[sqlite3.Connection]:
    if _connect_db is None:
        raise RuntimeError("aggregate catalog dependencies are not configured")
    return _connect_db()


def resequence_series_volumes(series_url: str) -> None:
    if _resequence_series_volumes is None:
        raise RuntimeError("aggregate catalog dependencies are not configured")
    _resequence_series_volumes(series_url)


def invalidate_catalog_indexes() -> None:
    if _invalidate_catalog_indexes is None:
        raise RuntimeError("aggregate catalog dependencies are not configured")
    _invalidate_catalog_indexes()


def is_volume_range_badge(label: str | None) -> bool:
    return aggregate_catalog_inference.is_volume_range_badge(label)


def is_aggregate_volume_title(title: str, url: str) -> bool:
    return aggregate_catalog_inference.is_aggregate_volume_title(title, url)


def is_aggregate_volume_catalog(url: str, details: PostDetails) -> bool:
    return aggregate_catalog_inference.is_aggregate_volume_catalog(
        url,
        details,
        connect_db=connect_db,
    )


def infer_aggregate_catalog_parent(url: str, details: PostDetails) -> str | None:
    return aggregate_catalog_inference.infer_aggregate_catalog_parent(
        url,
        details,
        connect_db=connect_db,
    )


def infer_aggregate_catalog_parent_from_db(url: str, title: str) -> str | None:
    return aggregate_catalog_inference.infer_aggregate_catalog_parent_from_db(
        url,
        title,
        connect_db=connect_db,
    )


def infer_aggregate_catalog_parents_from_db(
    candidates: list[tuple[str, str]],
) -> dict[str, str]:
    return aggregate_catalog_inference.infer_aggregate_catalog_parents_from_db(
        candidates,
        connect_db=connect_db,
    )


def aggregate_release_group(title: str, details: PostDetails) -> str:
    return aggregate_catalog_inference.aggregate_release_group(title, details)


def merge_aggregate_catalog_into_parent(
    parent_url: str,
    aggregate_url: str,
    details: PostDetails,
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
        group = aggregate_release_group(book["title"], details)
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


def merge_cached_aggregate_catalogs_into_parent(parent_url: str) -> int:
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
            if is_aggregate_volume_title(book["title"], book["url"])
            and infer_aggregate_catalog_parent_from_db(book["url"], book["title"])
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
        changes += merge_aggregate_catalog_into_parent(parent_url, book["url"], details)
    return changes


def reconcile_aggregate_volume_catalogs() -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT title, url, original_post_url, download_url
            FROM books
            WHERE entry_type = 'volume'
              AND original_post_url IS NOT NULL
              AND download_url IS NOT NULL
            """
        ).fetchall()
        candidates = [
            row
            for row in rows
            if is_aggregate_volume_title(row["title"], row["url"])
        ]
        before = connection.total_changes
        for row in candidates:
            aggregate_label = extract_catalog_label(row["title"])
            connection.execute(
                """
                UPDATE books
                SET entry_type = 'series',
                    original_post_url = NULL
                WHERE url = ?
                """,
                (row["url"],),
            )
            if aggregate_label:
                connection.execute(
                    """
                    DELETE FROM series_volumes
                    WHERE series_url = ?
                      AND lower(label) = lower(?)
                      AND download_url = ?
                    """,
                    (
                        row["original_post_url"],
                        aggregate_label,
                        row["download_url"],
                    ),
                )
            connection.execute(
                """
                UPDATE series_status
                SET metadata_checked_at = NULL
                WHERE series_url = ?
                """,
                (row["url"],),
            )
        changes = connection.total_changes - before
    affected_parents = {
        row["original_post_url"]
        for row in candidates
        if row["original_post_url"]
    }
    for parent_url in affected_parents:
        resequence_series_volumes(parent_url)
    if changes:
        invalidate_catalog_indexes()
    return changes


