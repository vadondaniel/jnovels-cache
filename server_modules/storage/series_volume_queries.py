from __future__ import annotations

import re
import sqlite3
import urllib.parse
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

from server_modules.normalization.label_utils import catalog_display_label, extract_catalog_label
from server_modules.normalization.release_groups import format_release_family
from server_modules.normalization.title_utils import parse_book_identity, series_identity_key
from server_modules.normalization.url_utils import canonical_page_url, is_internal_jnovels_url


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def list_series_volumes(
    series_url: str,
    *,
    connect_db: ConnectDb,
    infer_aggregate_catalog_parent_from_db: Callable[[str, str], str | None],
    filter_pdf_rows_when_epub_exists: Callable[[list[Any]], list[Any]],
) -> list[dict[str, Any]]:
    def add_book_rows(
        collected: dict[str, sqlite3.Row],
        connection: sqlite3.Connection,
        sql: str,
        params: tuple[Any, ...],
    ) -> None:
        for row in connection.execute(sql, params).fetchall():
            collected.setdefault(row["url"], row)

    def url_lookup_variants(url: str) -> set[str]:
        canonical = canonical_page_url(url)
        variants = {url, canonical}
        parsed = urllib.parse.urlsplit(canonical)
        if parsed.path != "/" and not parsed.path.endswith("/"):
            variants.add(
                urllib.parse.urlunsplit(parsed._replace(path=f"{parsed.path}/"))
            )
        return variants

    def chunks(values: list[str], size: int = 400) -> Iterator[list[str]]:
        for index in range(0, len(values), size):
            yield values[index : index + size]

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
        book_rows_by_url: dict[str, sqlite3.Row] = {}
        add_book_rows(
            book_rows_by_url,
            connection,
            """
            SELECT title, url, download_url, published_at, entry_type,
                   original_post_url
            FROM books
            WHERE original_post_url = ?
            """,
            (series_url,),
        )
        download_urls = sorted(
            {row["download_url"] for row in rows if row["download_url"]}
        )
        for batch in chunks(download_urls):
            placeholders = ", ".join("?" for _ in batch)
            add_book_rows(
                book_rows_by_url,
                connection,
                f"""
                SELECT title, url, download_url, published_at, entry_type,
                       original_post_url
                FROM books
                WHERE download_url IN ({placeholders})
                """,
                tuple(batch),
            )
        internal_url_variants = sorted(
            {
                variant
                for download_url in download_urls
                if is_internal_jnovels_url(download_url)
                for variant in url_lookup_variants(download_url)
            }
        )
        for batch in chunks(internal_url_variants):
            placeholders = ", ".join("?" for _ in batch)
            add_book_rows(
                book_rows_by_url,
                connection,
                f"""
                SELECT title, url, download_url, published_at, entry_type,
                       original_post_url
                FROM books
                WHERE url IN ({placeholders})
                """,
                tuple(batch),
            )
        labels = sorted({row["label"] for row in rows if row["label"]})
        for batch in chunks(labels, 80):
            label_conditions = " OR ".join(
                "title LIKE ? ESCAPE '\\'" for _ in batch
            )
            label_params = tuple(
                "%"
                + label.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
                for label in batch
            )
            add_book_rows(
                book_rows_by_url,
                connection,
                f"""
                SELECT title, url, download_url, published_at, entry_type,
                       original_post_url
                FROM books
                WHERE entry_type = 'volume'
                  AND ({label_conditions})
                """,
                label_params,
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

