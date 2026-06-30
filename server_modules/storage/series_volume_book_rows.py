from __future__ import annotations

import sqlite3
import urllib.parse
from collections.abc import Iterator
from typing import Any

from server_modules.normalization.url_utils import (
    canonical_page_url,
    is_internal_jnovels_url,
)


BOOK_ROW_SQL = """
    SELECT title, url, download_url, published_at, entry_type,
           original_post_url
    FROM books
"""


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


def collect_related_book_rows(
    connection: sqlite3.Connection,
    *,
    series_url: str,
    catalog_rows: list[dict[str, Any]],
) -> dict[str, sqlite3.Row]:
    book_rows_by_url: dict[str, sqlite3.Row] = {}
    add_book_rows(
        book_rows_by_url,
        connection,
        BOOK_ROW_SQL + " WHERE original_post_url = ?",
        (series_url,),
    )
    download_urls = sorted(
        {row["download_url"] for row in catalog_rows if row["download_url"]}
    )
    for batch in chunks(download_urls):
        placeholders = ", ".join("?" for _ in batch)
        add_book_rows(
            book_rows_by_url,
            connection,
            BOOK_ROW_SQL + f" WHERE download_url IN ({placeholders})",
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
            BOOK_ROW_SQL + f" WHERE url IN ({placeholders})",
            tuple(batch),
        )
    labels = sorted({row["label"] for row in catalog_rows if row["label"]})
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
            BOOK_ROW_SQL
            + f"""
              WHERE entry_type = 'volume'
                AND ({label_conditions})
            """,
            label_params,
        )
    return book_rows_by_url
