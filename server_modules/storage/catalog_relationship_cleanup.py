from __future__ import annotations

import sqlite3
import urllib.parse
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.normalization.title_utils import (
    series_identity_key,
    virtual_series_url,
)


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def reconcile_shared_index_subseries(
    *,
    shared_index_subseries: dict[str, str],
    connect_db: ConnectDb,
    build_fallback_catalog: Callable[[str], int],
    resequence_series_volumes: Callable[[str], None],
    mark_series_metadata_checked: Callable[[str], None],
    invalidate_catalog_indexes: Callable[[], None],
) -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT title, url, entry_type, original_post_url, download_url
            FROM books
            """
        ).fetchall()

    changes = 0
    for identity_key, index_url in shared_index_subseries.items():
        family = [
            row
            for row in rows
            if series_identity_key(row["title"], row["url"]) == identity_key
        ]
        if not family:
            continue
        virtual_url = virtual_series_url(identity_key)
        old_catalogs = {
            row["url"]
            for row in family
            if row["entry_type"] == "series" and row["url"] != virtual_url
        }
        family_downloads = {
            row["download_url"] for row in family if row["download_url"]
        }
        with connect_db() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                UPDATE books
                SET entry_type = 'volume', original_post_url = ?
                WHERE url = ?
                """,
                [(virtual_url, row["url"]) for row in family],
            )
            connection.executemany(
                "DELETE FROM series_volumes WHERE series_url = ?",
                [(url,) for url in old_catalogs],
            )
            connection.executemany(
                "DELETE FROM series_status WHERE series_url = ?",
                [(url,) for url in old_catalogs],
            )
            connection.executemany(
                "DELETE FROM series_volumes WHERE series_url = ? AND download_url = ?",
                [(index_url, download_url) for download_url in family_downloads],
            )
            changes += connection.total_changes - before
        build_fallback_catalog(virtual_url)
        resequence_series_volumes(virtual_url)
        mark_series_metadata_checked(virtual_url)
    invalidate_catalog_indexes()
    return changes


def reconcile_catalog_relationships(
    *,
    force: bool,
    catalog_reconcile_key: str,
    shared_index_subseries: dict[str, str],
    connect_db: ConnectDb,
    merge_overlapping_catalog_aliases: Callable[[], int],
    reconcile_legacy_volume_entries: Callable[..., int],
    split_conflated_subseries_catalogs: Callable[[], int],
    reconcile_aggregate_volume_catalogs: Callable[[], int],
    build_fallback_catalog: Callable[[str], int],
    resequence_series_volumes: Callable[[str], None],
    relabel_catalog_entries_from_books: Callable[[], int],
    mark_series_metadata_checked: Callable[[str], None],
    invalidate_catalog_indexes: Callable[[], None],
) -> int:
    with connect_db() as connection:
        if not force and connection.execute(
            "SELECT 1 FROM metadata WHERE key = ?",
            (catalog_reconcile_key,),
        ).fetchone():
            return 0
    with connect_db() as connection:
        before = connection.total_changes
        connection.execute(
            """
            DELETE FROM series_volumes
            WHERE download_url IN (
                'http://mp4directs.com',
                'http://mp4directs.com/',
                'https://mp4directs.com',
                'https://mp4directs.com/'
            )
               OR (
                    lower(label) = 'complete edition'
                    AND (
                        lower(download_url) LIKE 'http://mp4directs.com/forums/%'
                        OR lower(download_url) LIKE 'https://mp4directs.com/forums/%'
                        OR lower(download_url) LIKE 'http://www.mp4directs.com/forums/%'
                        OR lower(download_url) LIKE 'https://www.mp4directs.com/forums/%'
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM series_volumes AS numbered
                        WHERE numbered.series_url = series_volumes.series_url
                          AND (
                              lower(numbered.label) LIKE 'volume %'
                              OR lower(numbered.label) LIKE 'part %'
                          )
                    )
               )
            """
        )
        changes = connection.total_changes - before
    changes += merge_overlapping_catalog_aliases()
    changes += reconcile_legacy_volume_entries(force=True)
    changes += split_conflated_subseries_catalogs()
    changes += reconcile_aggregate_volume_catalogs()
    changes += reconcile_shared_index_subseries(
        shared_index_subseries=shared_index_subseries,
        connect_db=connect_db,
        build_fallback_catalog=build_fallback_catalog,
        resequence_series_volumes=resequence_series_volumes,
        mark_series_metadata_checked=mark_series_metadata_checked,
        invalidate_catalog_indexes=invalidate_catalog_indexes,
    )
    with connect_db() as connection:
        parent_urls = [
            row["original_post_url"]
            for row in connection.execute(
                """
                SELECT DISTINCT original_post_url
                FROM books
                WHERE entry_type = 'volume'
                  AND original_post_url IS NOT NULL
                """
            ).fetchall()
        ]
    changes += sum(build_fallback_catalog(url) for url in parent_urls)
    for parent_url in parent_urls:
        resequence_series_volumes(parent_url)
    changes += relabel_catalog_entries_from_books()
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                catalog_reconcile_key,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return changes


def cleanup_misclassified_series_tracking(
    *,
    base_url: str,
    excluded_search_paths: Iterable[str],
    connect_db: ConnectDb,
    reparent_series_alias: Callable[..., int],
    infer_series_parent: Callable[..., str | None],
    is_cached_series_url: Callable[[str], bool],
) -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT ss.series_url, b.original_post_url
            FROM series_status AS ss
            JOIN books AS b ON b.url = ss.series_url
            WHERE b.entry_type = 'volume'
              AND b.original_post_url IS NOT NULL
              AND b.original_post_url <> b.url
            """
        ).fetchall()
        external_parent_rows = connection.execute(
            """
            SELECT DISTINCT b.original_post_url, b.url, b.download_url
            FROM books AS b
            WHERE b.entry_type = 'volume'
              AND b.original_post_url IS NOT NULL
              AND lower(b.original_post_url) NOT LIKE 'https://jnovels.com/%'
              AND lower(b.original_post_url) NOT LIKE 'https://www.jnovels.com/%'
              AND lower(b.original_post_url) NOT LIKE 'virtual://jnovels/%'
            ORDER BY b.original_post_url, b.id
            """
        ).fetchall()

    changes = sum(
        reparent_series_alias(row["series_url"], row["original_post_url"])
        for row in rows
    )
    handled_external_parents: set[str] = set()
    for row in external_parent_rows:
        old_parent = row["original_post_url"]
        if old_parent in handled_external_parents:
            continue
        new_parent = infer_series_parent(
            row["url"],
            download_url=row["download_url"],
        )
        if not new_parent or not is_cached_series_url(new_parent):
            continue
        changes += reparent_series_alias(old_parent, new_parent)
        handled_external_parents.add(old_parent)

    excluded_urls = [
        urllib.parse.urljoin(base_url, path)
        for path in excluded_search_paths
    ]
    with connect_db() as connection:
        before = connection.total_changes
        connection.executemany(
            """
            DELETE FROM series_status
            WHERE series_url = ?
              AND NOT EXISTS (
                  SELECT 1 FROM series_volumes
                  WHERE series_volumes.series_url = series_status.series_url
              )
              AND NOT EXISTS (
                  SELECT 1 FROM books
                  WHERE books.original_post_url = series_status.series_url
              )
            """,
            [(url,) for url in excluded_urls],
        )
        changes += connection.total_changes - before
    return changes
