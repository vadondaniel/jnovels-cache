from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from server_modules.domain.models import PostDetails
from server_modules.normalization.title_utils import (
    parse_book_identity,
    series_identity_key,
    series_keys_are_related,
)


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def is_volume_range_badge(label: str | None) -> bool:
    return bool(label and re.search(r"\bVol\.\s+[0-9]+(?:\.[0-9]+)?\s*[–-]\s*[0-9]+", label))


def is_aggregate_volume_title(title: str, url: str) -> bool:
    _, title_volume_label = parse_book_identity(title, url)
    return is_volume_range_badge(title_volume_label) or bool(
        re.search(r"\bRevised Editions?\b", title, re.IGNORECASE)
    )


def is_aggregate_volume_catalog(
    url: str,
    details: PostDetails,
    *,
    connect_db: ConnectDb,
) -> bool:
    labels = {
        volume.label.casefold()
        for volume in details.volumes
        if re.match(r"^Volume\s+[0-9]", volume.label, re.IGNORECASE)
    }
    if len(labels) < 2:
        return False
    with connect_db() as connection:
        row = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (url,),
        ).fetchone()
    if not row:
        return False
    return is_aggregate_volume_title(row["title"], url)


def infer_aggregate_catalog_parent(
    url: str,
    details: PostDetails,
    *,
    connect_db: ConnectDb,
) -> str | None:
    labels = {
        volume.label.casefold()
        for volume in details.volumes
        if re.match(r"^Volume\s+[0-9]", volume.label, re.IGNORECASE)
    }
    if len(labels) < 2:
        return None
    with connect_db() as connection:
        book = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (url,),
        ).fetchone()
        if not book:
            return None
        child_key = series_identity_key(book["title"], url)
        rows = connection.execute(
            """
            SELECT sv.series_url, parent.title,
                   SUM(CASE WHEN lower(sv.label) IN ({}) THEN 1 ELSE 0 END)
                       AS matching_labels,
                   (
                       SELECT COUNT(*)
                       FROM series_volumes AS count_volume
                       WHERE count_volume.series_url = sv.series_url
                   ) AS volume_count
            FROM series_volumes AS sv
            LEFT JOIN books AS parent ON parent.url = sv.series_url
            WHERE sv.series_url <> ?
            GROUP BY sv.series_url
            HAVING matching_labels >= 2
            """.format(
                ", ".join("?" for _ in labels),
            ),
            (*labels, url),
        ).fetchall()
    related = [
        row
        for row in rows
        if series_keys_are_related(
            child_key,
            series_identity_key(row["title"], row["series_url"]),
        )
    ]
    if not related:
        return None
    return max(
        related,
        key=lambda row: (
            row["matching_labels"],
            row["volume_count"],
            row["series_url"],
        ),
    )["series_url"]


def infer_aggregate_catalog_parent_from_db(
    url: str,
    title: str,
    *,
    connect_db: ConnectDb,
) -> str | None:
    if not is_aggregate_volume_title(title, url):
        return None
    with connect_db() as connection:
        labels = {
            row["label"].casefold()
            for row in connection.execute(
                """
                SELECT label
                FROM series_volumes
                WHERE series_url = ?
                """,
                (url,),
            ).fetchall()
            if re.match(r"^Volume\s+[0-9]", row["label"], re.IGNORECASE)
        }
        if len(labels) < 2:
            return None
        child_key = series_identity_key(title, url)
        rows = connection.execute(
            """
            SELECT sv.series_url, parent.title,
                   SUM(CASE WHEN lower(sv.label) IN ({}) THEN 1 ELSE 0 END)
                       AS matching_labels,
                   (
                       SELECT COUNT(*)
                       FROM series_volumes AS count_volume
                       WHERE count_volume.series_url = sv.series_url
                   ) AS volume_count
            FROM series_volumes AS sv
            LEFT JOIN books AS parent ON parent.url = sv.series_url
            WHERE sv.series_url <> ?
            GROUP BY sv.series_url
            HAVING matching_labels >= 2
            """.format(", ".join("?" for _ in labels)),
            (*labels, url),
        ).fetchall()
    related = [
        row
        for row in rows
        if series_keys_are_related(
            child_key,
            series_identity_key(row["title"], row["series_url"]),
        )
    ]
    if not related:
        return None
    return max(
        related,
        key=lambda row: (
            row["matching_labels"],
            row["volume_count"],
            row["series_url"],
        ),
    )["series_url"]


def infer_aggregate_catalog_parents_from_db(
    candidates: list[tuple[str, str]],
    *,
    connect_db: ConnectDb,
) -> dict[str, str]:
    aggregate_candidates = [
        (url, title)
        for url, title in candidates
        if is_aggregate_volume_title(title, url)
    ]
    if not aggregate_candidates:
        return {}

    candidate_urls = [url for url, _title in aggregate_candidates]
    candidate_placeholders = ", ".join("?" for _ in candidate_urls)
    labels_by_url: dict[str, set[str]] = {url: set() for url in candidate_urls}
    with connect_db() as connection:
        label_rows = connection.execute(
            f"""
            SELECT series_url, label
            FROM series_volumes
            WHERE series_url IN ({candidate_placeholders})
            """,
            candidate_urls,
        ).fetchall()
        for row in label_rows:
            if re.match(r"^Volume\s+[0-9]", row["label"], re.IGNORECASE):
                labels_by_url[row["series_url"]].add(row["label"].casefold())

        labels_by_url = {
            url: labels
            for url, labels in labels_by_url.items()
            if len(labels) >= 2
        }
        if not labels_by_url:
            return {}

        all_labels = sorted(
            {label for labels in labels_by_url.values() for label in labels}
        )
        label_placeholders = ", ".join("?" for _ in all_labels)
        matching_rows = connection.execute(
            f"""
            SELECT sv.series_url, parent.title, lower(sv.label) AS label,
                   counts.volume_count
            FROM series_volumes AS sv
            JOIN (
                SELECT series_url, COUNT(*) AS volume_count
                FROM series_volumes
                GROUP BY series_url
            ) AS counts ON counts.series_url = sv.series_url
            LEFT JOIN books AS parent ON parent.url = sv.series_url
            WHERE lower(sv.label) IN ({label_placeholders})
            """,
            all_labels,
        ).fetchall()

    result: dict[str, str] = {}
    titles_by_url = dict(aggregate_candidates)
    for url, labels in labels_by_url.items():
        child_key = series_identity_key(titles_by_url[url], url)
        related: dict[str, tuple[Any, int]] = {}
        for row in matching_rows:
            parent_url = row["series_url"]
            if parent_url == url or row["label"] not in labels:
                continue
            if not series_keys_are_related(
                child_key,
                series_identity_key(row["title"], parent_url),
            ):
                continue
            current = related.get(parent_url)
            related[parent_url] = (
                row,
                (current[1] if current else 0) + 1,
            )
        eligible = [
            (row, matching_labels)
            for row, matching_labels in related.values()
            if matching_labels >= 2
        ]
        if eligible:
            result[url] = max(
                eligible,
                key=lambda item: (
                    item[1],
                    item[0]["volume_count"],
                    item[0]["series_url"],
                ),
            )[0]["series_url"]
    return result


def aggregate_release_group(title: str, details: PostDetails) -> str:
    base = "Revised edition" if re.search(
        r"\bRevised Editions?\b",
        title,
        re.IGNORECASE,
    ) else "Alternate edition"
    groups = {(volume.release_group or "").casefold() for volume in details.volumes}
    if groups and all(group.endswith("epub") for group in groups):
        return f"{base} EPUB"
    if groups and all(group.endswith("pdf") for group in groups):
        return f"{base} PDF"
    return base
