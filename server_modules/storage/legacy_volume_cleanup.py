from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from server_modules.normalization.title_utils import (
    book_page_format,
    parse_book_identity,
    series_identity_key,
)


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def reconcile_legacy_volume_entries(
    *,
    force: bool,
    legacy_volume_reconcile_key: str,
    connect_db: ConnectDb,
    is_aggregate_volume_title: Callable[[str, str], bool],
    infer_series_parent: Callable[..., str | None],
    is_cached_series_url: Callable[[str], bool],
    reparent_series_alias: Callable[..., int],
    build_fallback_catalog: Callable[[str], int],
) -> int:
    with connect_db() as connection:
        if not force and connection.execute(
            "SELECT 1 FROM metadata WHERE key = ?",
            (legacy_volume_reconcile_key,),
        ).fetchone():
            return 0
        rows = connection.execute(
            """
            SELECT title, url, download_url
            FROM books
            WHERE entry_type = 'series'
            ORDER BY id
            """
        ).fetchall()

    repaired = 0
    for row in rows:
        if is_aggregate_volume_title(row["title"], row["url"]):
            continue
        _, volume_label = parse_book_identity(row["title"], row["url"])
        if not volume_label:
            continue
        parent_url = infer_series_parent(
            row["url"],
            download_url=row["download_url"],
            volume_label_hint=re.sub(
                r"^Vol\.\s+",
                "Volume ",
                volume_label,
                flags=re.IGNORECASE,
            ),
        )
        if not parent_url or not is_cached_series_url(parent_url):
            continue
        with connect_db() as connection:
            connection.execute(
                """
                UPDATE books
                SET entry_type = 'volume',
                    original_post_url = ?
                WHERE url = ?
                """,
                (parent_url, row["url"]),
            )
        reparent_series_alias(row["url"], parent_url)
        repaired += 1

    repaired += group_orphan_volume_families(
        connect_db=connect_db,
        is_aggregate_volume_title=is_aggregate_volume_title,
        reparent_series_alias=reparent_series_alias,
        build_fallback_catalog=build_fallback_catalog,
    )

    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                legacy_volume_reconcile_key,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return repaired


def group_orphan_volume_families(
    *,
    connect_db: ConnectDb,
    is_aggregate_volume_title: Callable[[str, str], bool],
    reparent_series_alias: Callable[..., int],
    build_fallback_catalog: Callable[[str], int],
) -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT b.id, b.title, b.url, b.download_url, b.published_at,
                   (
                       SELECT COUNT(*)
                       FROM series_volumes AS sv
                       WHERE sv.series_url = b.url
                   ) AS catalog_count
            FROM books AS b
            WHERE b.entry_type = 'series'
              AND b.download_url IS NOT NULL
            ORDER BY b.published_at IS NULL, b.published_at, b.id
            """
        ).fetchall()

    families: dict[tuple[str, str, bool], list[sqlite3.Row]] = {}
    for row in rows:
        if is_aggregate_volume_title(row["title"], row["url"]):
            continue
        _, volume_label = parse_book_identity(row["title"], row["url"])
        page_format = book_page_format(row["title"], row["url"])
        identity = series_identity_key(row["title"], row["url"])
        if not volume_label or page_format not in {"epub", "pdf", "mixed"} or not identity:
            continue
        is_webnovel = "webnovel" in f"{row['title']} {row['url']}".casefold()
        families.setdefault((identity, page_format, is_webnovel), []).append(row)

    grouped = 0
    for family in families.values():
        if len(family) < 2:
            continue

        def release_number(row: sqlite3.Row) -> float:
            _, badge = parse_book_identity(row["title"], row["url"])
            match = re.search(r"[0-9]+(?:\.[0-9]+)?", badge or "")
            return float(match.group(0)) if match else float("inf")

        canonical = min(
            family,
            key=lambda row: (
                -(row["catalog_count"] > 1),
                release_number(row),
                row["published_at"] is None,
                row["published_at"] or "",
                row["id"],
            ),
        )
        _, canonical_badge = parse_book_identity(
            canonical["title"],
            canonical["url"],
        )
        canonical_label = re.sub(
            r"^Vol\.\s+",
            "Volume ",
            canonical_badge or "Volume 1",
            flags=re.IGNORECASE,
        )
        with connect_db() as connection:
            next_position = connection.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM series_volumes
                WHERE series_url = ?
                """,
                (canonical["url"],),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT OR IGNORE INTO series_volumes
                    (series_url, label, download_url, position, cached_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    canonical["url"],
                    canonical_label,
                    canonical["download_url"],
                    next_position,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        for row in family:
            if row["url"] == canonical["url"]:
                continue
            with connect_db() as connection:
                connection.execute(
                    """
                    UPDATE books
                    SET entry_type = 'volume',
                        original_post_url = ?
                    WHERE url = ?
                    """,
                    (canonical["url"], row["url"]),
                )
            reparent_series_alias(row["url"], canonical["url"])
            grouped += 1
        build_fallback_catalog(canonical["url"])
    return grouped
