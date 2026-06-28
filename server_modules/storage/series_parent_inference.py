from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from server_modules.normalization.title_utils import (
    book_page_format,
    parent_hint_aliases,
    parse_book_identity,
    series_identity_key,
    series_keys_are_related,
)


ConnectDb = Callable[[], AbstractContextManager[sqlite3.Connection]]


def infer_series_parent(
    url: str,
    parent_hints: list[str] | None = None,
    download_url: str | None = None,
    volume_label_hint: str | None = None,
    *,
    connect_db: ConnectDb,
) -> str | None:
    with connect_db() as connection:
        book = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (url,),
        ).fetchone()
        if not book:
            return None
        child_format = book_page_format(book["title"], url)
        child_key = series_identity_key(book["title"], url)
        if download_url:
            exact_parents = connection.execute(
                """
                SELECT sv.series_url,
                       parent.title,
                       (
                           SELECT COUNT(*)
                           FROM series_volumes AS sibling
                           WHERE sibling.series_url = sv.series_url
                       ) AS volume_count
                FROM series_volumes AS sv
                LEFT JOIN books AS parent ON parent.url = sv.series_url
                WHERE sv.download_url = ?
                  AND sv.series_url <> ?
                """,
                (download_url, url),
            ).fetchall()
            related_exact_parents = [
                candidate
                for candidate in exact_parents
                if series_keys_are_related(
                    child_key,
                    series_identity_key(
                        candidate["title"],
                        candidate["series_url"],
                    ),
                )
            ]
            if related_exact_parents:
                return max(
                    related_exact_parents,
                    key=lambda candidate: (
                        int(
                            book_page_format(
                                candidate["title"] or "",
                                candidate["series_url"],
                            )
                            in {child_format, "mixed"}
                        ),
                        int(
                            parse_book_identity(
                                candidate["title"] or "",
                                candidate["series_url"],
                            )[1]
                            is None
                        ),
                        candidate["volume_count"],
                        candidate["series_url"],
                    ),
                )["series_url"]
            established_exact_parents = [
                candidate
                for candidate in exact_parents
                if candidate["volume_count"] >= 2
            ]
            if established_exact_parents:
                return max(
                    established_exact_parents,
                    key=lambda candidate: (
                        int(
                            book_page_format(
                                candidate["title"] or "",
                                candidate["series_url"],
                            )
                            in {child_format, "mixed"}
                        ),
                        candidate["volume_count"],
                        candidate["series_url"],
                    ),
                )["series_url"]
        _, parsed_volume_label = parse_book_identity(book["title"], url)
        volume_label = parsed_volume_label or volume_label_hint
        if not volume_label:
            return None
        catalog_volume_label = re.sub(
            r"^Vol\.\s+",
            "Volume ",
            volume_label,
            flags=re.IGNORECASE,
        )
        hint_aliases = parent_hint_aliases(parent_hints or [], url)
        hint_keys = [key for _, key in hint_aliases]
        raw_alias_keys = [
            series_identity_key(hint, url)
            for hint in parent_hints or []
        ]
        child_keys = list(
            dict.fromkeys([child_key, *raw_alias_keys, *hint_keys])
        )
        candidates = connection.execute(
            """
            WITH candidate_urls AS (
                SELECT url
                FROM books
                WHERE entry_type = 'series'
                UNION
                SELECT original_post_url
                FROM books
                WHERE original_post_url IS NOT NULL
                UNION
                SELECT series_url
                FROM series_volumes
            )
            SELECT COALESCE(
                       (
                           SELECT parent.title
                           FROM books AS parent
                           WHERE parent.url = candidate_urls.url
                       ),
                       (
                           SELECT child.title
                           FROM books AS child
                           WHERE child.original_post_url = candidate_urls.url
                           ORDER BY child.published_at IS NULL,
                                    child.published_at DESC,
                                    child.id DESC
                           LIMIT 1
                       )
                   ) AS title,
                   candidate_urls.url,
                   (
                        SELECT COUNT(*)
                        FROM series_volumes AS count_volume
                        WHERE count_volume.series_url = candidate_urls.url
                   ) AS volume_count,
                   EXISTS (
                        SELECT 1
                        FROM series_volumes AS matching_volume
                        WHERE matching_volume.series_url = candidate_urls.url
                          AND lower(matching_volume.label) = lower(?)
                   ) AS has_matching_volume
            FROM candidate_urls
            WHERE candidate_urls.url <> ?
              AND EXISTS (
                   SELECT 1
                   FROM series_volumes AS sv
                   WHERE sv.series_url = candidate_urls.url
              )
            """,
            (catalog_volume_label, url),
        ).fetchall()
        child_alias_rows = connection.execute(
            """
            SELECT original_post_url, title, url
            FROM books
            WHERE entry_type = 'volume'
              AND original_post_url IS NOT NULL
            """
        ).fetchall()

    child_alias_counts: dict[tuple[str, str], int] = {}
    for child_alias in child_alias_rows:
        alias_key = series_identity_key(child_alias["title"], child_alias["url"])
        key = (child_alias["original_post_url"], alias_key)
        child_alias_counts[key] = child_alias_counts.get(key, 0) + 1

    matches: list[tuple[int, int, int, int, str]] = []
    for candidate in candidates:
        candidate_key = series_identity_key(candidate["title"], candidate["url"])
        candidate_words = candidate_key.split()
        candidate_compact = candidate_key.replace(" ", "")
        candidate_format = book_page_format(
            candidate["title"] or "",
            candidate["url"],
        )
        identity_matches = any(
            child_key == candidate_key
            for child_key in child_keys
        )
        title_prefix_matches = any(
            child_key.startswith(f"{candidate_key} ")
            for child_key in child_keys
        )
        alias_matches = (
            candidate_key != child_keys[0]
            and len(candidate_compact) >= 10
            and any(
                candidate_compact in child_key.replace(" ", "")
                for child_key in child_keys[1:]
            )
        )
        explicit_alias_matches = len(set(hint_keys)) >= 2 and any(
            series_keys_are_related(candidate_key, alias_key)
            or (
                len(candidate_compact) >= 10
                and candidate_compact in alias_key.replace(" ", "")
            )
            for alias_key in hint_keys
        )
        established_child_alias_matches = (
            child_alias_counts.get((candidate["url"], child_key), 0) >= 2
        )
        if (
            identity_matches
            and candidate["volume_count"] >= 2
            and candidate["has_matching_volume"]
        ) or (
            explicit_alias_matches
            and candidate["volume_count"] >= 1
        ) or (
            established_child_alias_matches
            and candidate["has_matching_volume"]
        ) or (
            ((title_prefix_matches and parsed_volume_label) or alias_matches)
            and (
                len(candidate_words) >= 5
                or (
                    len(candidate_words) >= 2
                    and candidate["volume_count"] >= 3
                    and candidate["has_matching_volume"]
                )
            )
        ):
            matches.append(
                (
                    int(candidate_format in {child_format, "mixed"}),
                    int(
                        parse_book_identity(
                            candidate["title"] or "",
                            candidate["url"],
                        )[1]
                        is None
                    ),
                    len(candidate_words),
                    candidate["volume_count"],
                    candidate["url"],
                )
            )
    return max(matches, default=(0, 0, 0, 0, ""))[4] or None


