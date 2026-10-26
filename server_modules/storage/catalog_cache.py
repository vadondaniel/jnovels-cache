from __future__ import annotations

import sqlite3
import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from server_modules.normalization.release_groups import release_group_format_kind
from server_modules.normalization.title_utils import (
    normalize_display_title,
    parse_book_identity,
    series_identity_key,
)
from server_modules.normalization.url_utils import is_counted_catalog_volume, is_internal_jnovels_url


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]

DB_PATH: Path | None = None
_connect_db: ConnectDb | None = None
_invalidate_book_filter_counts: Callable[[], None] | None = None
_catalog_index_lock = threading.Lock()
_catalog_index_cache: tuple[
    Path,
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[tuple[str, str], int],
] | None = None


def configure(
    *,
    db_path: Path,
    connect_db: ConnectDb,
    invalidate_book_filter_counts: Callable[[], None],
) -> None:
    global DB_PATH
    global _connect_db
    global _invalidate_book_filter_counts
    DB_PATH = db_path
    _connect_db = connect_db
    _invalidate_book_filter_counts = invalidate_book_filter_counts


def connect_db() -> AbstractContextManager[sqlite3.Connection]:
    if _connect_db is None:
        raise RuntimeError("catalog cache dependencies are not configured")
    return _connect_db()


def row_value(row: Any, key: str) -> Any | None:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def filter_pdf_rows_when_epub_exists(rows: list[Any]) -> list[Any]:
    epub_series = {
        row_value(row, "series_url")
        for row in rows
        if release_group_format_kind(row["release_group"]) == "epub"
    }
    if not epub_series:
        return rows
    return [
        row
        for row in rows
        if release_group_format_kind(row["release_group"]) != "pdf"
        or row_value(row, "series_url") not in epub_series
    ]


def invalidate_catalog_indexes() -> None:
    global _catalog_index_cache
    with _catalog_index_lock:
        _catalog_index_cache = None
    if _invalidate_book_filter_counts is not None:
        _invalidate_book_filter_counts()


def get_catalog_indexes() -> tuple[
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[tuple[str, str], int],
]:
    global _catalog_index_cache
    if DB_PATH is None:
        raise RuntimeError("catalog cache dependencies are not configured")
    with _catalog_index_lock:
        if (
            _catalog_index_cache is not None
            and _catalog_index_cache[0] == DB_PATH
            and len(_catalog_index_cache) == 7
        ):
            return _catalog_index_cache[1:]

        with connect_db() as connection:
            rows = connection.execute(
                """
        SELECT sv.series_url,
               sv.download_url,
               sv.label,
               sv.release_group,
               sv.position,
               counts.volume_count,
               b.title
                FROM series_volumes AS sv
                JOIN (
                    SELECT series_url,
                           SUM(is_counted_catalog_volume(label, download_url)) AS volume_count
                    FROM series_volumes
                    GROUP BY series_url
                ) AS counts ON counts.series_url = sv.series_url
                LEFT JOIN books AS b ON b.url = sv.series_url
                ORDER BY counts.volume_count DESC, sv.series_url, sv.position
                """
            ).fetchall()
            volume_books = connection.execute(
                """
                SELECT title, url, original_post_url
                FROM books
                WHERE entry_type = 'volume'
                """
            ).fetchall()

        rows = filter_pdf_rows_when_epub_exists(list(rows))
        visible_volume_counts: dict[str, int] = {}
        for row in rows:
            if is_counted_catalog_volume(row["label"], row["download_url"]):
                visible_volume_counts[row["series_url"]] = (
                    visible_volume_counts.get(row["series_url"], 0) + 1
                )

        catalog_index: dict[str, tuple[str, int]] = {}
        download_index: dict[str, tuple[str, int]] = {}
        volume_counts: dict[str, int] = {}
        category_labels: dict[tuple[str, str], set[str]] = {}
        category_positions: dict[tuple[str, str], int] = {}
        for row in rows:
            candidate = (
                row["series_url"],
                visible_volume_counts.get(row["series_url"], 0),
            )
            volume_counts[row["series_url"]] = candidate[1]
            key = series_identity_key(row["title"], row["series_url"])
            if key and candidate[1] > catalog_index.get(key, ("", 0))[1]:
                catalog_index[key] = candidate
            download_index.setdefault(row["download_url"], candidate)
            category_key = (
                row["series_url"],
                (row["release_group"] or "").casefold(),
            )
            if not is_internal_jnovels_url(row["download_url"]):
                category_labels.setdefault(category_key, set()).add(
                    row["label"].casefold()
                )
                category_positions[category_key] = min(
                    row["position"],
                    category_positions.get(category_key, row["position"]),
                )

        download_category_counts = {
            row["download_url"]: len(
                category_labels.get(
                    (
                        row["series_url"],
                        (row["release_group"] or "").casefold(),
                    ),
                    set(),
                )
            )
            for row in rows
        }
        primary_category_counts = {
            series_url: len(category_labels[category_key])
            for series_url, category_key in {
                series_url: min(
                    (
                        key
                        for key in category_labels
                        if key[0] == series_url
                    ),
                    key=lambda key: category_positions.get(key, 0),
                )
                for series_url in volume_counts
                if any(key[0] == series_url for key in category_labels)
            }.items()
        }

        duplicate_volume_numbers: dict[tuple[str, str], set[str]] = {}
        for book in volume_books:
            parent_url = book["original_post_url"]
            if not parent_url:
                continue
            _, badge = parse_book_identity(book["title"], book["url"])
            match = re.fullmatch(r"Vol\. ([0-9]+(?:\.[0-9]+)?)", badge or "")
            if not match:
                continue
            identity = (parent_url, match.group(1))
            title_identity = normalize_display_title(book["title"])
            title_identity = title_identity.removesuffix(" PDF").casefold()
            duplicate_volume_numbers.setdefault(identity, set()).add(title_identity)
        duplicate_volume_counts = {
            identity: len(titles)
            for identity, titles in duplicate_volume_numbers.items()
        }

        _catalog_index_cache = (
            DB_PATH,
            catalog_index,
            download_index,
            volume_counts,
            download_category_counts,
            primary_category_counts,
            duplicate_volume_counts,
        )
        return (
            catalog_index,
            download_index,
            volume_counts,
            download_category_counts,
            primary_category_counts,
            duplicate_volume_counts,
        )


def get_series_catalog_index() -> dict[str, tuple[str, int]]:
    catalog_index, *_ = get_catalog_indexes()
    return catalog_index


def get_download_catalog_index() -> dict[str, tuple[str, int]]:
    _, download_index, *_ = get_catalog_indexes()
    return download_index
