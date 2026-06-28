from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from server_modules.domain.models import PostDetails, SeriesVolume
from server_modules.normalization.label_utils import extract_catalog_label
from server_modules.normalization.title_utils import (
    parse_book_identity,
    series_identity_key,
    series_keys_are_related,
)


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
    return bool(label and re.search(r"\bVol\.\s+[0-9]+(?:\.[0-9]+)?\s*[–-]\s*[0-9]+", label))


def is_aggregate_volume_title(title: str, url: str) -> bool:
    _, title_volume_label = parse_book_identity(title, url)
    return is_volume_range_badge(title_volume_label) or bool(
        re.search(r"\bRevised Editions?\b", title, re.IGNORECASE)
    )


def is_aggregate_volume_catalog(url: str, details: PostDetails) -> bool:
    labels = {
        volume.label.casefold()
        for volume in details.volumes
        if re.match(r"^Volume\s+[0-9]", volume.label, re.IGNORECASE)
    }
    if len(labels) < 2:
        return False
    with connect_db() as connection:
        row = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (url,),
        ).fetchone()
    if not row:
        return False
    return is_aggregate_volume_title(row["title"], url)


def infer_aggregate_catalog_parent(url: str, details: PostDetails) -> str | None:
    labels = {
        volume.label.casefold()
        for volume in details.volumes
        if re.match(r"^Volume\s+[0-9]", volume.label, re.IGNORECASE)
    }
    if len(labels) < 2:
        return None
    with connect_db() as connection:
        book = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (url,),
        ).fetchone()
        if not book:
            return None
        child_key = series_identity_key(book["title"], url)
        rows = connection.execute(
            """
            SELECT sv.series_url, parent.title,
                   SUM(CASE WHEN lower(sv.label) IN ({}) THEN 1 ELSE 0 END)
                       AS matching_labels,
                   (
                       SELECT COUNT(*)
                       FROM series_volumes AS count_volume
                       WHERE count_volume.series_url = sv.series_url
                   ) AS volume_count
            FROM series_volumes AS sv
            LEFT JOIN books AS parent ON parent.url = sv.series_url
            WHERE sv.series_url <> ?
            GROUP BY sv.series_url
            HAVING matching_labels >= 2
            """.format(
                ", ".join("?" for _ in labels),
            ),
            (*labels, url),
        ).fetchall()
    related = [
        row
        for row in rows
        if series_keys_are_related(
            child_key,
            series_identity_key(row["title"], row["series_url"]),
        )
    ]
    if not related:
        return None
    return max(
        related,
        key=lambda row: (
            row["matching_labels"],
            row["volume_count"],
            row["series_url"],
        ),
    )["series_url"]


def infer_aggregate_catalog_parent_from_db(url: str, title: str) -> str | None:
    if not is_aggregate_volume_title(title, url):
        return None
    with connect_db() as connection:
        labels = {
            row["label"].casefold()
            for row in connection.execute(
                """
                SELECT label
                FROM series_volumes
                WHERE series_url = ?
                """,
                (url,),
            ).fetchall()
            if re.match(r"^Volume\s+[0-9]", row["label"], re.IGNORECASE)
        }
        if len(labels) < 2:
            return None
        child_key = series_identity_key(title, url)
        rows = connection.execute(
            """
            SELECT sv.series_url, parent.title,
                   SUM(CASE WHEN lower(sv.label) IN ({}) THEN 1 ELSE 0 END)
                       AS matching_labels,
                   (
                       SELECT COUNT(*)
                       FROM series_volumes AS count_volume
                       WHERE count_volume.series_url = sv.series_url
                   ) AS volume_count
            FROM series_volumes AS sv
            LEFT JOIN books AS parent ON parent.url = sv.series_url
            WHERE sv.series_url <> ?
            GROUP BY sv.series_url
            HAVING matching_labels >= 2
            """.format(", ".join("?" for _ in labels)),
            (*labels, url),
        ).fetchall()
    related = [
        row
        for row in rows
        if series_keys_are_related(
            child_key,
            series_identity_key(row["title"], row["series_url"]),
        )
    ]
    if not related:
        return None
    return max(
        related,
        key=lambda row: (
            row["matching_labels"],
            row["volume_count"],
            row["series_url"],
        ),
    )["series_url"]


def infer_aggregate_catalog_parents_from_db(
    candidates: list[tuple[str, str]],
) -> dict[str, str]:
    aggregate_candidates = [
        (url, title)
        for url, title in candidates
        if is_aggregate_volume_title(title, url)
    ]
    if not aggregate_candidates:
        return {}

    candidate_urls = [url for url, _title in aggregate_candidates]
    candidate_placeholders = ", ".join("?" for _ in candidate_urls)
    labels_by_url: dict[str, set[str]] = {url: set() for url in candidate_urls}
    with connect_db() as connection:
        label_rows = connection.execute(
            f"""
            SELECT series_url, label
            FROM series_volumes
            WHERE series_url IN ({candidate_placeholders})
            """,
            candidate_urls,
        ).fetchall()
        for row in label_rows:
            if re.match(r"^Volume\s+[0-9]", row["label"], re.IGNORECASE):
                labels_by_url[row["series_url"]].add(row["label"].casefold())

        labels_by_url = {
            url: labels
            for url, labels in labels_by_url.items()
            if len(labels) >= 2
        }
        if not labels_by_url:
            return {}

        all_labels = sorted(
            {label for labels in labels_by_url.values() for label in labels}
        )
        label_placeholders = ", ".join("?" for _ in all_labels)
        matching_rows = connection.execute(
            f"""
            SELECT sv.series_url, parent.title, lower(sv.label) AS label,
                   counts.volume_count
            FROM series_volumes AS sv
            JOIN (
                SELECT series_url, COUNT(*) AS volume_count
                FROM series_volumes
                GROUP BY series_url
            ) AS counts ON counts.series_url = sv.series_url
            LEFT JOIN books AS parent ON parent.url = sv.series_url
            WHERE lower(sv.label) IN ({label_placeholders})
            """,
            all_labels,
        ).fetchall()

    result: dict[str, str] = {}
    titles_by_url = dict(aggregate_candidates)
    for url, labels in labels_by_url.items():
        child_key = series_identity_key(titles_by_url[url], url)
        related: dict[str, tuple[Any, int]] = {}
        for row in matching_rows:
            parent_url = row["series_url"]
            if parent_url == url or row["label"] not in labels:
                continue
            if not series_keys_are_related(
                child_key,
                series_identity_key(row["title"], parent_url),
            ):
                continue
            current = related.get(parent_url)
            related[parent_url] = (
                row,
                (current[1] if current else 0) + 1,
            )
        eligible = [
            (row, matching_labels)
            for row, matching_labels in related.values()
            if matching_labels >= 2
        ]
        if eligible:
            result[url] = max(
                eligible,
                key=lambda item: (
                    item[1],
                    item[0]["volume_count"],
                    item[0]["series_url"],
                ),
            )[0]["series_url"]
    return result


def aggregate_release_group(title: str, details: PostDetails) -> str:
    base = "Revised edition" if re.search(
        r"\bRevised Editions?\b",
        title,
        re.IGNORECASE,
    ) else "Alternate edition"
    groups = {(volume.release_group or "").casefold() for volume in details.volumes}
    if groups and all(group.endswith("epub") for group in groups):
        return f"{base} EPUB"
    if groups and all(group.endswith("pdf") for group in groups):
        return f"{base} PDF"
    return base


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


