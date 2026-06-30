from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from server_modules.normalization.label_utils import catalog_display_label, extract_catalog_label
from server_modules.normalization.release_groups import format_release_family
from server_modules.normalization.title_utils import parse_book_identity, series_identity_key
from server_modules.normalization.url_utils import canonical_page_url, is_internal_jnovels_url
from server_modules.storage.series_volume_book_rows import collect_related_book_rows


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def list_series_volumes(
    series_url: str,
    *,
    connect_db: ConnectDb,
    infer_aggregate_catalog_parent_from_db: Callable[[str, str], str | None],
    filter_pdf_rows_when_epub_exists: Callable[[list[Any]], list[Any]],
) -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT label, download_url, position, cached_at, release_group
                FROM series_volumes
                WHERE series_url = ?
                ORDER BY position
                """,
                (series_url,),
            ).fetchall()
        ]
        series_book = connection.execute(
            """
            SELECT title, url, download_url, published_at, entry_type,
                   original_post_url
            FROM books
            WHERE url = ?
            """,
            (series_url,),
        ).fetchone()
        book_rows_by_url = collect_related_book_rows(
            connection,
            series_url=series_url,
            catalog_rows=rows,
        )

    target_key = series_identity_key(
        series_book["title"] if series_book else None,
        series_url,
    )
    series_title = (
        parse_book_identity(series_book["title"], series_url)[0]
        if series_book
        else None
    )
    if series_book and "short story collection" in series_book["title"].casefold():
        for volume in rows:
            volume["label"] = re.sub(
                r"^Short Story Collection\s+",
                "",
                volume["label"],
                flags=re.IGNORECASE,
            )
    matches: dict[str, list[sqlite3.Row]] = {}
    family_downloads: dict[str, sqlite3.Row] = {}
    internal_posts: dict[str, sqlite3.Row] = {}
    book_rows = sorted(
        book_rows_by_url.values(),
        key=lambda book: (
            book["entry_type"] == "volume",
            book["published_at"] or "",
            book["url"],
        ),
        reverse=True,
    )
    for book in book_rows:
        internal_posts.setdefault(canonical_page_url(book["url"]), book)
        unrelated = (
            canonical_page_url(book["original_post_url"] or "") !=
            canonical_page_url(series_url)
            and series_identity_key(book["title"], book["url"]) != target_key
        )
        aggregate_parent_url = (
            infer_aggregate_catalog_parent_from_db(book["url"], book["title"])
            if book["entry_type"] == "series"
            else None
        )
        if unrelated and aggregate_parent_url != series_url:
            continue
        is_volume_book = book["entry_type"] == "volume"
        if is_volume_book and book["download_url"]:
            family_downloads.setdefault(book["download_url"], book)
        elif aggregate_parent_url == series_url and book["download_url"]:
            family_downloads.setdefault(book["download_url"], book)
        label = extract_catalog_label(book["title"])
        if is_volume_book and label:
            matches.setdefault(label.casefold(), []).append(book)

    if series_book and series_book["download_url"]:
        family_downloads.setdefault(series_book["download_url"], series_book)

    label_categories: dict[str, set[str]] = {}
    for volume in rows:
        format_family = format_release_family(volume["release_group"])
        category = (
            f"__format__:{format_family}"
            if format_family is not None
            else (volume["release_group"] or "").casefold() or "__default__"
        )
        label_categories.setdefault(volume["label"].casefold(), set()).add(category)

    for volume in rows:
        internal_reference = is_internal_jnovels_url(volume["download_url"])
        volume["is_internal_reference"] = internal_reference
        book = (
            internal_posts.get(canonical_page_url(volume["download_url"]))
            if internal_reference
            else family_downloads.get(volume["download_url"])
        )
        label_key = volume["label"].casefold()
        if (
            book is None
            and not internal_reference
            and len(label_categories.get(label_key, set())) <= 1
        ):
            candidates = matches.get(label_key, [])
            book = max(
                candidates,
                key=lambda candidate: candidate["published_at"] or "",
                default=None,
            )
        volume["published_at"] = book["published_at"] if book else None
        volume["post_url"] = book["url"] if book else None
        volume["display_label"] = catalog_display_label(
            volume["label"],
            series_title,
            volume["release_group"],
        )

    dated_formats: dict[tuple[str, str], dict[str, Any]] = {}
    for volume in rows:
        format_family = format_release_family(volume["release_group"])
        if format_family is not None and volume["published_at"]:
            dated_formats.setdefault(
                (format_family, volume["label"].casefold()),
                volume,
            )
    for volume in rows:
        format_family = format_release_family(volume["release_group"])
        if format_family is None or volume["published_at"]:
            continue
        dated = dated_formats.get((format_family, volume["label"].casefold()))
        if dated:
            volume["published_at"] = dated["published_at"]
            volume["post_url"] = dated["post_url"]
    return filter_pdf_rows_when_epub_exists(rows)

