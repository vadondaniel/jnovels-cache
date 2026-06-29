from __future__ import annotations

from collections.abc import Callable
from typing import Any

from server_modules.config import SHARED_INDEX_SUBSERIES
from server_modules.normalization.label_utils import prefer_catalog_volume_badge
from server_modules.normalization.text_utils import decode_string_list
from server_modules.normalization.title_utils import (
    is_distinct_subseries_title,
    parse_book_identity,
    qualify_numbered_subseries_badge,
    series_identity_key,
)
from server_modules.normalization.url_utils import is_internal_jnovels_url


CatalogIndexes = tuple[
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]


def project_listed_books(
    rows: list[Any],
    *,
    get_catalog_indexes: Callable[[], CatalogIndexes],
    infer_aggregate_catalog_parents_from_db: Callable[
        [list[tuple[str, str]]],
        dict[str, str],
    ],
) -> list[dict[str, Any]]:
    books = [dict(row) for row in rows]
    (
        catalog_index,
        download_catalog_index,
        volume_counts,
        download_category_counts,
        primary_category_counts,
    ) = get_catalog_indexes()
    aggregate_parent_urls = infer_aggregate_catalog_parents_from_db(
        [
            (book["url"], book["title"])
            for book in books
            if book["entry_type"] == "series"
        ]
    )
    for book in books:
        book["associated_names"] = decode_string_list(book["associated_names"])
        book["genres"] = decode_string_list(book["genres"])
        book["is_favorite"] = bool(book["is_favorite"])
        book["is_hidden"] = bool(book["is_hidden"])
        series_title, volume_label = parse_book_identity(book["title"], book["url"])
        if book["parent_series_title"]:
            series_title, _ = parse_book_identity(
                book["parent_series_title"],
                book["original_post_url"] or book["url"],
            )
        if book["latest_series_title"]:
            latest_series_title, latest_volume_label = parse_book_identity(
                book["latest_series_title"],
                book["original_post_url"] or book["url"],
            )
            if latest_volume_label and not is_distinct_subseries_title(
                series_title,
                latest_series_title,
            ):
                series_title = latest_series_title
        volume_label = prefer_catalog_volume_badge(
            volume_label,
            book["catalog_volume_label"],
        )
        volume_label = qualify_numbered_subseries_badge(
            book["title"],
            volume_label,
        )
        series_url = (
            book["original_post_url"]
            if book["entry_type"] == "volume"
            else book["url"] if book["entry_type"] == "series" else None
        )
        aggregate_parent_url = aggregate_parent_urls.get(book["url"])
        if aggregate_parent_url:
            series_url = aggregate_parent_url
        catalog = catalog_index.get(series_identity_key(book["title"], book["url"]))
        download_catalog = download_catalog_index.get(book["download_url"] or "")
        book_key = series_identity_key(book["title"], book["url"])
        may_infer_catalog = not series_url or volume_counts.get(series_url, 0) <= 1
        if (
            may_infer_catalog
            and download_catalog
            and download_catalog[1] > volume_counts.get(series_url or "", 0)
        ):
            series_url = download_catalog[0]
        if (
            may_infer_catalog
            and catalog
            and catalog[1] > volume_counts.get(series_url or "", 0)
        ):
            series_url = catalog[0]
        book["series_title"] = series_title
        book["series_url"] = series_url
        book["series_page_url"] = SHARED_INDEX_SUBSERIES.get(
            book_key,
            series_url if series_url and is_internal_jnovels_url(series_url) else None,
        )
        book["is_aggregate_catalog"] = bool(aggregate_parent_url)
        book["volume_count"] = volume_counts.get(series_url or "", 0)
        if book["entry_type"] == "series":
            volume_label = None
        book["volume_label"] = volume_label
        primary_catalog_count = primary_category_counts.get(
            series_url or "",
            book["volume_count"],
        )
        book["catalog_count"] = download_category_counts.get(
            book["download_url"] or "",
            primary_catalog_count,
        )
        title_and_url = f"{book['title']} {book['url']}".casefold()
        book["catalog_kind"] = (
            "downloads"
            if "webnovel" in title_and_url
            or "[pdf]" in title_and_url
            or "[epub]" in title_and_url
            or "pdfepub" in title_and_url
            else "volumes"
        )
        book["is_standalone"] = (
            book["entry_type"] == "series" and book["volume_count"] == 1
        )
    return books
