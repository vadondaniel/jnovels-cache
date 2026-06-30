from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from server_modules.domain.models import SeriesVolume
from server_modules.normalization.label_utils import extract_catalog_label
from server_modules.normalization.title_utils import (
    parse_book_identity,
    series_identity_key,
)


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def split_conflated_subseries_catalogs(
    *,
    connect_db: ConnectDb,
    replace_series_volumes: Callable[[str, list[SeriesVolume]], None],
    build_fallback_catalog: Callable[[str], int],
    invalidate_catalog_indexes: Callable[[], None],
) -> int:
    """Restore independent catalog pages absorbed by loose title-prefix matching."""
    changes = 0
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT child.title, child.url, child.original_post_url, child.download_url,
                   child.published_at, parent.title AS parent_title
            FROM books AS child
            JOIN books AS parent ON parent.url = child.original_post_url
            WHERE child.entry_type = 'volume'
              AND child.original_post_url IS NOT NULL
              AND child.download_url IS NOT NULL
            ORDER BY child.published_at IS NULL, child.published_at, child.id
            """
        ).fetchall()

    families: dict[tuple[str, str], list[sqlite3.Row]] = {}
    candidates: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        child_key = series_identity_key(row["title"], row["url"])
        parent_key = series_identity_key(row["parent_title"], row["original_post_url"])
        if not child_key or child_key == parent_key:
            continue
        family_key = (row["original_post_url"], child_key)
        families.setdefault(family_key, []).append(row)
        _, volume_label = parse_book_identity(row["title"], row["url"])
        if volume_label is None:
            candidates[family_key] = row

    for family_key, candidate in candidates.items():
        family = families[family_key]
        labeled = [
            row
            for row in family
            if extract_catalog_label(row["title"])
        ]
        if len(labeled) < 1 or len(family) < 2:
            continue
        series_url = candidate["url"]
        with connect_db() as connection:
            before = connection.total_changes
            connection.execute(
                """
                UPDATE books
                SET entry_type = 'series', original_post_url = NULL
                WHERE url = ?
                """,
                (series_url,),
            )
            connection.executemany(
                """
                UPDATE books
                SET entry_type = 'volume', original_post_url = ?
                WHERE url = ?
                """,
                [(series_url, row["url"]) for row in family if row["url"] != series_url],
            )
            changes += connection.total_changes - before

        initial_label = extract_catalog_label(candidate["title"]) or "Volume 1"
        initial = [SeriesVolume(initial_label, candidate["download_url"], 0)]
        replace_series_volumes(series_url, initial)
        build_fallback_catalog(series_url)
        parent_url = family_key[0]
        with connect_db() as connection:
            family_downloads = {row["download_url"] for row in family}
            connection.executemany(
                "DELETE FROM series_volumes WHERE series_url = ? AND download_url = ?",
                [(parent_url, value) for value in family_downloads],
            )
        build_fallback_catalog(parent_url)
        invalidate_catalog_indexes()
    return changes
