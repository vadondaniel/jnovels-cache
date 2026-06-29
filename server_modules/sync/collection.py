from __future__ import annotations

from collections.abc import Callable
from typing import Any

from server_modules.domain.models import Book


def collect_sync_books(
    mode: str,
    start_page: int,
    max_pages: int,
    delay: float,
    known_urls: set[str],
    *,
    sync_state: dict[str, Any],
    fetch_page: Callable[[int], list[Book]],
    sleep: Callable[[float], None],
    on_page: Callable[[int, list[Book]], None] | None = None,
    cached_frontier: int = 0,
) -> list[Book]:
    collected: list[Book] = []
    fetched_pages: dict[int, list[Book]] = {}
    source_page_had_results: dict[int, bool] = {}

    def fetch(page: int) -> list[Book]:
        if page not in fetched_pages:
            sync_state["page"] = page
            fetched_pages[page] = fetch_page(page)
            source_page_had_results[page] = bool(fetched_pages[page])
            if not source_page_had_results[page]:
                sync_state["search_end_page"] = page
            sync_state["pages_completed"] = int(sync_state["pages_completed"]) + 1
            sync_state["items_seen"] = int(sync_state["items_seen"]) + len(
                fetched_pages[page],
            )
            if on_page:
                on_page(page, fetched_pages[page])
        return fetched_pages[page]

    def add_new(books: list[Book]) -> int:
        new_books = [book for book in books if book.url not in known_urls]
        collected.extend(new_books)
        known_urls.update(book.url for book in new_books)
        sync_state["new_items"] = int(sync_state["new_items"]) + len(new_books)
        return len(new_books)

    if mode == "catchup":
        for page in range(1, max_pages + 1):
            books = fetch(page)
            if not source_page_had_results[page]:
                break
            page_had_known = any(book.url in known_urls for book in books)
            add_new(books)
            if page_had_known:
                break
            if page < max_pages:
                sleep(delay)
        return collected

    if mode == "extend":
        if not known_urls:
            for page in range(1, max_pages + 1):
                books = fetch(page)
                if not source_page_had_results[page]:
                    break
                add_new(books)
                if page < max_pages:
                    sleep(delay)
            return collected

        first_known_page = 1
        page_size = 0
        for page in range(1, min(max_pages, 10) + 1):
            books = fetch(page)
            if not source_page_had_results[page]:
                return collected
            page_size = max(page_size, len(books))
            page_had_known = any(book.url in known_urls for book in books)
            add_new(books)
            if page_had_known:
                first_known_page = page
                break
            sleep(delay)

        if page_size == 0:
            return collected

        estimated_frontier = cached_frontier or (
            (len(known_urls) + page_size - 1) // page_size
        )
        estimated_frontier = max(first_known_page + 1, estimated_frontier)
        candidate_page = max(first_known_page + 1, estimated_frontier - 2)
        page = candidate_page
        uncached_pages_added = 0
        while uncached_pages_added < max_pages:
            books = fetch(page)
            if not source_page_had_results[page]:
                break
            new_count = add_new(books)
            if new_count:
                uncached_pages_added += 1
            page += 1
            if uncached_pages_added < max_pages:
                sleep(delay)
        return collected

    for offset in range(max_pages):
        page = start_page + offset
        books = fetch(page)
        if not source_page_had_results[page]:
            break
        collected.extend(books)
        sync_state["new_items"] = int(sync_state["new_items"]) + sum(
            book.url not in known_urls for book in books
        )
        known_urls.update(book.url for book in books)
        if offset < max_pages - 1:
            sleep(delay)
    return collected
