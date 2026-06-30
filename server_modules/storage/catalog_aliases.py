from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.domain.models import SeriesVolume
from server_modules.storage import catalog_subseries_split
from server_modules.normalization.title_utils import (
    book_page_format,
    parent_hint_aliases,
    parse_book_identity,
    series_identity_key,
    series_keys_are_related,
)


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def merge_overlapping_catalog_aliases(
    series_url: str | None = None,
    *,
    connect_db: ConnectDb,
    is_aggregate_volume_title: Callable[[str, str], bool],
    delete_book_urls: Callable[[sqlite3.Connection, set[str]], int],
    invalidate_catalog_indexes: Callable[[], None],
) -> int:
    """Collapse older/typo catalog pages whose downloads identify the same series."""
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT sv.series_url AS url, b.title, sv.download_url
            FROM series_volumes AS sv
            LEFT JOIN books AS b ON b.url = sv.series_url
            WHERE b.url IS NULL OR b.entry_type = 'series'
            ORDER BY sv.series_url, sv.position
            """
        ).fetchall()
        child_rows = connection.execute(
            """
            SELECT original_post_url, title, url
            FROM books
            WHERE entry_type = 'volume'
              AND original_post_url IS NOT NULL
            """
        ).fetchall()

    titles: dict[str, str] = {}
    cached_book_urls: set[str] = set()
    downloads: dict[str, set[str]] = {}
    catalog_identity_keys: dict[str, set[str]] = {}
    for row in rows:
        titles[row["url"]] = row["title"] or ""
        if row["title"]:
            cached_book_urls.add(row["url"])
        downloads.setdefault(row["url"], set()).add(row["download_url"])
        identity = series_identity_key(row["title"], row["url"])
        if identity:
            catalog_identity_keys.setdefault(row["url"], set()).add(identity)
    for child in child_rows:
        identity = series_identity_key(child["title"], child["url"])
        if identity:
            catalog_identity_keys.setdefault(
                child["original_post_url"],
                set(),
            ).add(identity)

    aliases: dict[str, str] = {}
    ordered_urls = sorted(
        downloads,
        key=lambda value: (
            -len(downloads[value]),
            -int(value in cached_book_urls),
            value,
        ),
    )
    url_rank = {url: rank for rank, url in enumerate(ordered_urls)}
    download_owners: dict[str, list[str]] = {}
    for url, catalog_downloads in downloads.items():
        for download_url in catalog_downloads:
            download_owners.setdefault(download_url, []).append(url)
    candidate_pairs: set[tuple[str, str]] = set()
    for owners in download_owners.values():
        for first_index, first_url in enumerate(owners):
            for second_url in owners[first_index + 1 :]:
                canonical_url, alias_url = sorted(
                    (first_url, second_url),
                    key=url_rank.__getitem__,
                )
                if series_url is None or series_url in {canonical_url, alias_url}:
                    candidate_pairs.add((canonical_url, alias_url))

    for canonical_url, alias_url in sorted(
        candidate_pairs,
        key=lambda pair: (url_rank[pair[0]], url_rank[pair[1]]),
    ):
        if canonical_url in aliases or alias_url in aliases:
            continue
        canonical_format = book_page_format(titles[canonical_url], canonical_url)
        alias_format = book_page_format(titles[alias_url], alias_url)
        if canonical_format not in {alias_format, "mixed"} and alias_format != "mixed":
            continue
        shared = downloads[canonical_url] & downloads[alias_url]
        if is_aggregate_volume_title(titles[alias_url], alias_url):
            continue
        _, alias_volume_label = parse_book_identity(
            titles[alias_url],
            alias_url,
        )
        alias_identity = series_identity_key(titles[alias_url], alias_url)
        related_legacy_volume = (
            alias_volume_label is not None
            and len(downloads[canonical_url]) >= len(downloads[alias_url]) * 3
            and any(
                series_keys_are_related(alias_identity, candidate_identity)
                for candidate_identity in catalog_identity_keys.get(canonical_url, set())
            )
        )
        substantial_overlap = (
            len(shared) >= 2
            and len(shared) * 5 >= len(downloads[alias_url]) * 4
        )
        if not substantial_overlap and not related_legacy_volume:
            continue
        aliases[alias_url] = canonical_url

    changes = 0
    for alias_url, canonical_url in aliases.items():
        with connect_db() as connection:
            before = connection.total_changes
            alias_book = connection.execute(
                "SELECT title FROM books WHERE url = ?",
                (alias_url,),
            ).fetchone()
            _, alias_volume_label = parse_book_identity(
                alias_book["title"] if alias_book else "",
                alias_url,
            )
            connection.execute(
                "UPDATE books SET original_post_url = ? WHERE original_post_url = ?",
                (canonical_url, alias_url),
            )
            preference = connection.execute(
                "SELECT favorite, hidden FROM series_preferences WHERE series_url = ?",
                (alias_url,),
            ).fetchone()
            if preference:
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                connection.execute(
                    """
                    INSERT INTO series_preferences (series_url, favorite, hidden, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(series_url) DO UPDATE SET
                        favorite = MAX(favorite, excluded.favorite),
                        hidden = MAX(hidden, excluded.hidden),
                        updated_at = excluded.updated_at
                    """,
                    (canonical_url, preference["favorite"], preference["hidden"], now),
                )
            connection.execute(
                "DELETE FROM series_preferences WHERE series_url = ?",
                (alias_url,),
            )
            if alias_volume_label:
                connection.execute(
                    "DELETE FROM series_volumes WHERE series_url = ?",
                    (alias_url,),
                )
                connection.execute(
                    "DELETE FROM series_status WHERE series_url = ?",
                    (alias_url,),
                )
                connection.execute(
                    """
                    UPDATE books
                    SET entry_type = 'volume', original_post_url = ?
                    WHERE url = ?
                    """,
                    (canonical_url, alias_url),
                )
            else:
                delete_book_urls(connection, {alias_url})
            changes += connection.total_changes - before
    if changes:
        invalidate_catalog_indexes()
    return changes


def split_conflated_subseries_catalogs(
    *,
    connect_db: ConnectDb,
    replace_series_volumes: Callable[[str, list[SeriesVolume]], None],
    build_fallback_catalog: Callable[[str], int],
    invalidate_catalog_indexes: Callable[[], None],
) -> int:
    return catalog_subseries_split.split_conflated_subseries_catalogs(
        connect_db=connect_db,
        replace_series_volumes=replace_series_volumes,
        build_fallback_catalog=build_fallback_catalog,
        invalidate_catalog_indexes=invalidate_catalog_indexes,
    )


def merge_explicit_alias_catalogs(
    canonical_series_url: str,
    hints: list[str],
    source_url: str,
    *,
    connect_db: ConnectDb,
    reparent_series_alias: Callable[..., int],
) -> int:
    aliases = parent_hint_aliases(hints, source_url)
    alias_keys = {key for _, key in aliases}
    if len(alias_keys) < 2:
        return 0
    source_format = book_page_format("", source_url)
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT b.title, b.url
            FROM books AS b
            WHERE b.entry_type = 'series'
              AND b.url <> ?
              AND EXISTS (
                  SELECT 1
                  FROM series_volumes AS sv
                  WHERE sv.series_url = b.url
              )
            """,
            (canonical_series_url,),
        ).fetchall()

    merged = 0
    for row in rows:
        candidate_key = series_identity_key(row["title"], row["url"])
        candidate_format = book_page_format(row["title"], row["url"])
        if candidate_key not in alias_keys or candidate_format not in {
            source_format,
            "mixed",
        }:
            continue
        _, volume_label = parse_book_identity(row["title"], row["url"])
        if volume_label:
            with connect_db() as connection:
                connection.execute(
                    """
                    UPDATE books
                    SET entry_type = 'volume',
                        original_post_url = ?
                    WHERE url = ?
                    """,
                    (canonical_series_url, row["url"]),
                )
        merged += reparent_series_alias(
            row["url"],
            canonical_series_url,
            dedupe_labels=True,
        )
    return merged


