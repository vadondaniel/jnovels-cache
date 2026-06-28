from __future__ import annotations

import re
from collections.abc import Callable


def parse_search_terms(query: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r'"([^"]+)"|([\w%\\]+)', query, flags=re.UNICODE):
        quoted, unquoted = match.groups()
        term = " ".join(quoted.split()) if quoted is not None else unquoted
        if term:
            terms.append(term)
    return terms


def fts_query_for_term(term: str) -> str | None:
    tokens = re.findall(r"\w+", term, flags=re.UNICODE)
    if not tokens:
        return None
    escaped_tokens = [token.replace('"', '""') for token in tokens]
    if len(escaped_tokens) == 1:
        return f'"{escaped_tokens[0]}"'
    return f'"{" ".join(escaped_tokens)}"'


def book_fts_filter(
    term: str,
    *,
    include_family_titles: bool,
) -> tuple[str, tuple[str, ...]] | None:
    fts_query = fts_query_for_term(term)
    if not fts_query:
        return None
    conditions = [
        """
        books.id IN (
            SELECT rowid
            FROM books_fts
            WHERE books_fts MATCH ?
        )
        """
    ]
    params = [fts_query]
    if include_family_titles:
        conditions.extend(
            [
                """
                EXISTS (
                    SELECT 1
                    FROM books AS parent
                    WHERE parent.url = books.original_post_url
                      AND parent.id IN (
                          SELECT rowid
                          FROM books_fts
                          WHERE books_fts MATCH ?
                      )
                )
                """,
                """
                EXISTS (
                    SELECT 1
                    FROM books AS sibling
                    WHERE sibling.original_post_url =
                          COALESCE(books.original_post_url, books.url)
                      AND sibling.id IN (
                          SELECT rowid
                          FROM books_fts
                          WHERE books_fts MATCH ?
                      )
                )
                """,
            ]
        )
        params.extend([fts_query, fts_query])
    return f"({' OR '.join(conditions)})", tuple(params)


def book_filter(
    query: str,
    entry_filter: str = "all",
    exact_url: str | None = None,
    *,
    books_fts_available: Callable[[], bool],
) -> tuple[str, tuple[str, ...]]:
    if exact_url:
        return " WHERE books.url = ?", (exact_url,)

    conditions: list[str] = []
    params: list[str] = []
    if query:
        for term in parse_search_terms(query):
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if term.isdecimal():
                if books_fts_available():
                    fts_filter = book_fts_filter(term, include_family_titles=False)
                    if fts_filter:
                        condition, condition_params = fts_filter
                        conditions.append(condition)
                        params.extend(condition_params)
                conditions.append(
                    """
                    (
                        books.title LIKE ? ESCAPE '\\'
                        OR books.associated_names LIKE ? ESCAPE '\\'
                    )
                    """
                )
                params.extend([f"%{escaped}%"] * 2)
                continue
            if books_fts_available():
                fts_filter = book_fts_filter(term, include_family_titles=True)
                if fts_filter:
                    condition, condition_params = fts_filter
                    conditions.append(condition)
                    params.extend(condition_params)
            conditions.append(
                """
                (
                    books.title LIKE ? ESCAPE '\\'
                    OR books.associated_names LIKE ? ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1
                        FROM books AS parent
                        WHERE parent.url = books.original_post_url
                          AND parent.title LIKE ? ESCAPE '\\'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM books AS sibling
                        WHERE sibling.original_post_url =
                              COALESCE(books.original_post_url, books.url)
                          AND sibling.title LIKE ? ESCAPE '\\'
                    )
                )
                """
            )
            params.extend([f"%{escaped}%"] * 4)

    preference_series_url = "COALESCE(books.original_post_url, books.url)"
    if entry_filter == "hidden":
        conditions.append(
            f"""
            EXISTS (
                SELECT 1
                FROM series_preferences AS preference
                WHERE preference.series_url = {preference_series_url}
                  AND preference.hidden = 1
            )
            """
        )
    else:
        conditions.append(
            f"""
            NOT EXISTS (
                SELECT 1
                FROM series_preferences AS preference
                WHERE preference.series_url = {preference_series_url}
                  AND preference.hidden = 1
            )
            """
        )

    if entry_filter == "favorites":
        conditions.append(
            f"""
            EXISTS (
                SELECT 1
                FROM series_preferences AS preference
                WHERE preference.series_url = {preference_series_url}
                  AND preference.favorite = 1
            )
            """
        )
    elif entry_filter == "series":
        conditions.append("entry_type = 'series'")
    elif entry_filter == "volumes":
        conditions.append("entry_type = 'volume'")
    elif entry_filter == "pending":
        conditions.append(
            """
            (
                detail_cached_at IS NULL
                OR entry_type IS NULL
                OR download_url IS NULL
            )
            """
        )

    if not conditions:
        return "", ()
    return f" WHERE {' AND '.join(conditions)}", tuple(params)
