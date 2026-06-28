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
