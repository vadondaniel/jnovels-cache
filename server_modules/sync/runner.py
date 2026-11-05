from __future__ import annotations

import sqlite3
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from server_modules.domain.models import Book, PostDetails


@dataclass(frozen=True)
class SyncRunnerConfig:
    auto_repair_limit: int
    auto_repair_max_attempts: int
    auto_series_index_limit: int
    auto_series_refresh_limit: int


@dataclass(frozen=True)
class SyncRunnerDeps:
    sync_state: dict[str, Any]
    set_sync_started: Callable[[int, int, float, str], None]
    cache_content_signature: Callable[[], str]
    save_sync_checkpoint: Callable[[dict[str, Any]], None]
    clear_sync_checkpoint: Callable[[], None]
    get_series_refresh_urls: Callable[[int], list[str]]
    get_incomplete_detail_urls: Callable[..., list[str]]
    fetch_series_index: Callable[[], list[Book]]
    store_series_index: Callable[[list[Book]], tuple[int, int]]
    add_series_index_candidates: Callable[[list[Book]], list[str]]
    get_series_index_candidates: Callable[[int], list[Book]]
    get_cached_urls: Callable[[], set[str]]
    get_search_scan_frontier: Callable[[], int]
    get_book_source_orders: Callable[[list[str]], dict[str, int]]
    observed_source_page_shift: Callable[[int, dict[str, int]], int]
    apply_observed_front_shift: Callable[[int, int, int], tuple[int, int]]
    upsert_books: Callable[..., int]
    set_metadata: Callable[[str, str], None]
    delete_metadata: Callable[[str], None]
    collect_sync_books: Callable[..., list[Book]]
    series_index_is_initialized: Callable[[], bool]
    series_index_refresh_due: Callable[[], bool]
    fetch_post_details: Callable[[str], PostDetails]
    validate_post_details: Callable[[PostDetails], None]
    update_book_details: Callable[[str, PostDetails], None]
    record_detail_failure: Callable[[str, Exception], None]
    sleep: Callable[[float], None]
    check_source_changes: Callable[[float, bool], None]
    get_forced_series_urls: Callable[[int], list[str]]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_sync_worker(
    start_page: int,
    max_pages: int,
    delay: float,
    mode: str,
    detail_urls_override: list[str] | None,
    series_urls_override: list[str] | None,
    recovery: bool,
    *,
    config: SyncRunnerConfig,
    deps: SyncRunnerDeps,
    force_full_refresh: bool = False,
) -> None:
    sync_state = deps.sync_state
    initial_cache_signature: str | None = None
    completion_notice: str | None = None
    try:
        deps.set_sync_started(start_page, max_pages, delay, mode)
        initial_cache_signature = deps.cache_content_signature()
        checkpoint: dict[str, Any] = {
            "version": 1,
            "status": "active",
            "mode": mode,
            "start_page": start_page,
            "max_pages": max_pages,
            "delay": delay,
            "phase": "details" if detail_urls_override is not None else "search",
            "page": start_page,
            "pages_completed": 0,
            "pending_detail_urls": list(dict.fromkeys(detail_urls_override or [])),
            "pending_series_urls": list(dict.fromkeys(series_urls_override or [])),
            "started_at": sync_state["started_at"],
            "recovery": recovery,
        }
        deps.save_sync_checkpoint(checkpoint)

        if not recovery and series_urls_override is None and mode not in {"repair", "series_discovery"}:
            deps.check_source_changes(delay, mode == "series_refresh")
        sync_state["phase"] = "details" if detail_urls_override is not None else "search"
        sync_state["phase_started_at"] = utc_timestamp()

        scheduled_series_urls: set[str] = set(series_urls_override or [])
        selected_order = list(series_urls_override or [])
        if detail_urls_override is not None:
            detail_urls = list(dict.fromkeys(detail_urls_override))
        elif mode == "series_refresh":
            detail_urls = []
            if series_urls_override is None:
                selected_order = (deps.get_forced_series_urls(max_pages) if force_full_refresh
                                  else deps.get_series_refresh_urls(max_pages))
                scheduled_series_urls.update(selected_order)
        elif mode == "repair":
            detail_urls = deps.get_incomplete_detail_urls(max_pages)
        elif mode == "series_discovery":
            sync_state["phase"] = "index"
            sync_state["phase_started_at"] = utc_timestamp()
            index_entries = deps.fetch_series_index()
            index_total, index_new = deps.store_series_index(index_entries)
            detail_urls = deps.add_series_index_candidates(
                deps.get_series_index_candidates(max_pages)
            )
            completion_notice = (
                f"Checked {index_total} indexed series"
                + (f"; {index_new} new to the index." if index_new else ".")
            )
        else:
            known_urls = deps.get_cached_urls()
            scan_frontier = deps.get_search_scan_frontier()
            applied_front_shift = 0

            def persist_page(page: int, page_books: list[Book]) -> None:
                nonlocal applied_front_shift, scan_frontier
                previous_orders = deps.get_book_source_orders(
                    [book.url for book in page_books]
                )
                observed_shift = deps.observed_source_page_shift(page, previous_orders)
                scan_frontier, applied_front_shift = deps.apply_observed_front_shift(
                    scan_frontier,
                    applied_front_shift,
                    observed_shift,
                )
                deps.upsert_books(page_books, source_page=page)
                if page_books and page <= scan_frontier + 1:
                    scan_frontier = max(scan_frontier, page)
                    deps.set_metadata("search_scan_frontier", str(scan_frontier))
                cached_urls = deps.get_cached_urls()
                page_books[:] = [
                    book for book in page_books if book.url in cached_urls
                ]
                pending = checkpoint["pending_detail_urls"]
                pending.extend(book.url for book in page_books if book.url not in pending)
                sync_state["queued_details"] = len(pending)
                checkpoint["page"] = page
                checkpoint["pages_completed"] = sync_state["pages_completed"]
                deps.save_sync_checkpoint(checkpoint)

            books = deps.collect_sync_books(
                mode,
                start_page,
                max_pages,
                delay,
                known_urls,
                persist_page,
                scan_frontier,
            )
            cached_urls = deps.get_cached_urls()
            detail_urls = list(
                dict.fromkeys(
                    book.url for book in books if book.url in cached_urls
                )
            )
            detail_urls.extend(
                deps.get_incomplete_detail_urls(
                    config.auto_repair_limit,
                    max_attempts=config.auto_repair_max_attempts,
                    exclude_urls=set(detail_urls),
                )
            )
            selected_order = deps.get_series_refresh_urls(config.auto_series_refresh_limit)
            scheduled_series_urls.update(selected_order)
            if deps.series_index_is_initialized():
                if deps.series_index_refresh_due():
                    try:
                        deps.store_series_index(deps.fetch_series_index())
                    except (
                        urllib.error.URLError,
                        TimeoutError,
                        OSError,
                        ValueError,
                    ):
                        pass
                index_candidates = deps.get_series_index_candidates(
                    config.auto_series_index_limit
                )
                detail_urls.extend(deps.add_series_index_candidates(index_candidates))
                detail_urls = list(dict.fromkeys(detail_urls))
        sync_state["phase"] = "details"
        sync_state["phase_started_at"] = utc_timestamp()
        sync_state["details_total"] = len(detail_urls)
        sync_state["queued_details"] = len(detail_urls)
        sync_state["queued_series"] = len(scheduled_series_urls)
        checkpoint["phase"] = "details"
        checkpoint["pending_detail_urls"] = list(dict.fromkeys(detail_urls))
        checkpoint["pending_series_urls"] = selected_order
        deps.save_sync_checkpoint(checkpoint)
        discovered_series_urls: set[str] = set()
        detail_url_set = set(detail_urls)
        for index, url in enumerate(detail_urls):
            deps.sleep(delay)
            completed = False
            try:
                details = deps.fetch_post_details(url)
                deps.validate_post_details(details)
                deps.update_book_details(url, details)
                if details.entry_type == "series":
                    discovered_series_urls.add(url)
                elif details.original_post_url:
                    discovered_series_urls.add(details.original_post_url)
                completed = True
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
                sqlite3.Error,
            ) as exc:
                deps.record_detail_failure(url, exc)
                sync_state["detail_errors"] += 1
                completed = True
            finally:
                if completed:
                    sync_state["details_completed"] += 1
                    sync_state["queued_details"] = len(detail_urls) - index - 1
                    sync_state["queued_series"] = len(
                        (scheduled_series_urls | discovered_series_urls)
                        - detail_url_set
                    )
                    checkpoint["pending_detail_urls"] = detail_urls[index + 1 :]
                    checkpoint["pending_series_urls"] = selected_order + sorted(discovered_series_urls - scheduled_series_urls)
                    deps.save_sync_checkpoint(checkpoint)

        # Keep oldest-first selection order, including across checkpoint recovery.
        missing = (discovered_series_urls | scheduled_series_urls) - detail_url_set
        missing_series_urls = [url for url in selected_order if url in missing]
        missing_series_urls.extend(sorted(missing - set(missing_series_urls)))
        sync_state["phase"] = "series"
        sync_state["phase_started_at"] = utc_timestamp()
        sync_state["series_total"] = len(missing_series_urls)
        sync_state["queued_series"] = len(missing_series_urls)
        checkpoint["phase"] = "series"
        checkpoint["pending_detail_urls"] = []
        checkpoint["pending_series_urls"] = missing_series_urls
        deps.save_sync_checkpoint(checkpoint)
        for index, series_url in enumerate(missing_series_urls):
            deps.sleep(delay)
            completed = False
            try:
                details = deps.fetch_post_details(series_url)
                deps.validate_post_details(details)
                deps.update_book_details(series_url, details)
                completed = True
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
                sqlite3.Error,
            ) as exc:
                deps.record_detail_failure(series_url, exc)
                sync_state["series_errors"] += 1
                completed = True
            finally:
                if completed:
                    sync_state["series_completed"] += 1
                    sync_state["queued_series"] = len(missing_series_urls) - index - 1
                    checkpoint["pending_series_urls"] = missing_series_urls[index + 1 :]
                    deps.save_sync_checkpoint(checkpoint)

        finished = utc_timestamp()
        sync_state["cache_changed"] = (
            deps.cache_content_signature() != initial_cache_signature
        )
        sync_state["finished_at"] = finished
        sync_state["phase"] = None
        sync_state["phase_started_at"] = None
        sync_state["queued_details"] = 0
        sync_state["queued_series"] = 0
        if recovery:
            sync_state["notice"] = (
                "Recovered saved work from an interrupted sync; "
                "the original search may have stopped early."
            )
            deps.set_metadata("last_sync_recovered_at", finished)
        else:
            if sync_state["search_end_page"] is not None:
                sync_state["notice"] = (
                    "Reached the end of JNovels search results "
                    f"at page {sync_state['search_end_page']}."
                )
            elif completion_notice:
                sync_state["notice"] = completion_notice
            deps.set_metadata("last_sync_at", finished)
            deps.set_metadata("last_sync_pages", str(sync_state["pages_completed"]))
            deps.set_metadata("last_detail_errors", str(sync_state["detail_errors"]))
            deps.set_metadata("last_series_errors", str(sync_state["series_errors"]))
            deps.delete_metadata("last_sync_interrupted_at")
        deps.clear_sync_checkpoint()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        if initial_cache_signature is not None:
            sync_state["cache_changed"] = (
                deps.cache_content_signature() != initial_cache_signature
            )
        sync_state["error"] = str(exc)
        sync_state["finished_at"] = utc_timestamp()
        sync_state["phase"] = None
        sync_state["phase_started_at"] = None
