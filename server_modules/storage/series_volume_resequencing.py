from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from server_modules.normalization.label_utils import normalize_volume_number


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


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
