from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.normalization.label_utils import extract_catalog_label
from server_modules.normalization.release_groups import release_group_format_kind
from server_modules.normalization.title_utils import book_page_format


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def reconcile_series_downloads(
    connect_db: ConnectDb,
    invalidate_book_filter_counts: Callable[[], None],
    series_url: str,
) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        volume_rows = connection.execute(
            """
            SELECT label, download_url, release_group
            FROM series_volumes
            WHERE series_url = ?
            ORDER BY position
            """,
            (series_url,),
        ).fetchall()
        downloads_by_label: dict[tuple[str, str], str | None] = {}

        def record_download(label: str | None, format_key: str, download_url: str) -> None:
            if not label:
                return
            key = (label.casefold(), format_key)
            if key in downloads_by_label:
                downloads_by_label[key] = None
            else:
                downloads_by_label[key] = download_url

        for row in volume_rows:
            format_key = release_group_format_kind(row["release_group"]) or ""
            record_download(row["label"], format_key, row["download_url"])
            base_label = extract_catalog_label(row["label"])
            if base_label and base_label.casefold() != row["label"].casefold():
                record_download(base_label, format_key, row["download_url"])
        children = connection.execute(
            """
            SELECT url, title, download_url
            FROM books
            WHERE original_post_url = ?
            """,
            (series_url,),
        ).fetchall()
        updates = []
        for row in children:
            label = extract_catalog_label(row["title"])
            label_key = (label or "").casefold()
            child_format = book_page_format(row["title"], row["url"]) or ""
            download_url = (
                downloads_by_label.get((label_key, child_format))
                or downloads_by_label.get((label_key, ""))
            )
            if download_url and download_url != row["download_url"]:
                updates.append((download_url, now, now, row["url"]))
        connection.executemany(
            """
            UPDATE books
            SET download_url = ?,
                detail_cached_at = ?,
                detail_attempts = 0,
                detail_last_attempt_at = ?,
                detail_last_error = NULL
            WHERE url = ?
            """,
            updates,
        )
    if updates:
        invalidate_book_filter_counts()
    return len(updates)
