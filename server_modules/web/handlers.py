from __future__ import annotations

import json
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from typing import Any


def create_app_handler(backend: Any) -> type[SimpleHTTPRequestHandler]:
    class AppHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(
                *args,
                directory=str(backend.STATIC_DIR),
                **kwargs,
            )

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/books":
                params = urllib.parse.parse_qs(parsed.query)
                query = params.get("q", [""])[0].strip()
                exact_url = params.get("entry_url", [""])[0].strip() or None
                sort = params.get("sort", ["published_desc"])[0]
                entry_filter = params.get("filter", ["all"])[0].strip().casefold()
                if entry_filter not in {
                    "all",
                    "series",
                    "volumes",
                    "pending",
                    "favorites",
                    "hidden",
                }:
                    entry_filter = "all"
                filter_counts = backend.count_book_filters()
                page = backend.parse_positive_int(params.get("page", ["1"])[0], 1)
                page_size = min(
                    backend.parse_positive_int(
                        params.get("page_size", [str(backend.DEFAULT_PAGE_SIZE)])[0],
                        backend.DEFAULT_PAGE_SIZE,
                    ),
                    backend.MAX_PAGE_SIZE,
                )
                count = backend.count_books(query, entry_filter, exact_url)
                pages = max(1, (count + page_size - 1) // page_size)
                page = min(page, pages)
                books = backend.list_books(
                    query,
                    sort,
                    page_size,
                    (page - 1) * page_size,
                    entry_filter,
                    exact_url,
                )
                self.send_json(
                    {
                        "books": books,
                        "count": count,
                        "page": page,
                        "page_size": page_size,
                        "pages": pages,
                        "filter": entry_filter,
                        "filter_counts": filter_counts,
                    }
                )
                return
            if parsed.path == "/api/status":
                with backend.connect_db() as connection:
                    count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
                    metadata = backend.get_public_metadata(connection=connection)
                    health = backend.get_cache_health(connection=connection)
                self.send_json(
                    {
                        "sync": dict(backend.sync_state),
                        "count": count,
                        "metadata": metadata,
                        "health": health,
                    }
                )
                return
            if parsed.path == "/api/repair-failures":
                failures = backend.list_repair_failures()
                self.send_json({"failures": failures, "count": len(failures)})
                return
            if parsed.path == "/api/series":
                params = urllib.parse.parse_qs(parsed.query)
                series_url = params.get("url", [""])[0].strip()
                if not series_url:
                    self.send_json(
                        {"error": "Missing series URL."},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                volumes = backend.list_series_volumes(series_url)
                counted_volumes = sum(
                    backend.is_counted_catalog_volume(
                        volume["label"],
                        volume["download_url"],
                    )
                    for volume in volumes
                )
                self.send_json(
                    {
                        "series_url": series_url,
                        "volumes": volumes,
                        "count": counted_volumes,
                    }
                )
                return
            if parsed.path == "/":
                self.path = "/index.html"
            super().do_GET()

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in {
                "/api/sync",
                "/api/repair-entry",
                "/api/series-preference",
                "/api/refresh-series",
            }:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                payload = self.read_json()
                if parsed.path == "/api/series-preference":
                    series_url = str(payload.get("series_url", "")).strip()
                    if not series_url or not backend.is_cached_series_url(series_url):
                        self.send_json(
                            {"error": "Cached series not found."},
                            HTTPStatus.NOT_FOUND,
                        )
                        return
                    favorite_value = payload.get("favorite")
                    hidden_value = payload.get("hidden")
                    if favorite_value is not None and not isinstance(favorite_value, bool):
                        raise ValueError("Invalid favorite preference")
                    if hidden_value is not None and not isinstance(hidden_value, bool):
                        raise ValueError("Invalid hidden preference")
                    if favorite_value is None and hidden_value is None:
                        raise ValueError("Missing preference")
                    preference = backend.set_series_preference(
                        series_url,
                        favorite=favorite_value,
                        hidden=hidden_value,
                    )
                    self.send_json(preference)
                    return

                if parsed.path == "/api/refresh-series":
                    series_url = str(payload.get("series_url", "")).strip()
                    delay = min(max(float(payload.get("delay", 1.0)), 0.5), 10.0)
                    if not series_url or not backend.is_cached_series_url(series_url):
                        self.send_json(
                            {"error": "Cached series not found."},
                            HTTPStatus.NOT_FOUND,
                        )
                        return
                    started = backend.start_sync_thread(
                        1,
                        1,
                        delay,
                        "series_refresh",
                        series_urls_override=[series_url],
                        name="jnovels-refresh-series",
                    )
                    if not started:
                        self.send_json(
                            {"error": "A sync is already running."},
                            HTTPStatus.CONFLICT,
                        )
                        return
                    self.send_json({"ok": True}, HTTPStatus.ACCEPTED)
                    return

                if parsed.path == "/api/repair-entry":
                    url = str(payload.get("url", "")).strip()
                    delay = min(max(float(payload.get("delay", 1.0)), 0.5), 10.0)
                    if not url or not backend.is_cached_book_url(url):
                        self.send_json(
                            {"error": "Cached entry not found."},
                            HTTPStatus.NOT_FOUND,
                        )
                        return
                    started = backend.start_sync_thread(
                        1,
                        1,
                        delay,
                        "repair",
                        [url],
                        name="jnovels-repair-entry",
                    )
                    if not started:
                        self.send_json(
                            {"error": "A sync is already running."},
                            HTTPStatus.CONFLICT,
                        )
                        return
                    self.send_json({"ok": True}, HTTPStatus.ACCEPTED)
                    return

                mode = str(payload.get("mode", "catchup")).strip().casefold()
                if mode not in {
                    "catchup",
                    "extend",
                    "manual",
                    "repair",
                    "series_refresh",
                    "series_discovery",
                }:
                    raise ValueError("Unknown sync mode")
                start_page = min(max(int(payload.get("start_page", 1)), 1), 10000)
                max_limit = (
                    2000
                    if mode in {"repair", "series_refresh", "series_discovery"}
                    else 200
                )
                max_pages = min(max(int(payload.get("max_pages", 20)), 1), max_limit)
                delay = min(max(float(payload.get("delay", 1.0)), 0.5), 10.0)
                automatic = payload.get("automatic") is True
            except (ValueError, TypeError, json.JSONDecodeError):
                self.send_json(
                    {"error": "Invalid sync options."},
                    HTTPStatus.BAD_REQUEST,
                )
                return

            if automatic:
                if mode != "catchup":
                    self.send_json(
                        {"error": "Automatic sync only supports catch-up mode."},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                started, reason, retry_after = backend.start_auto_catchup(
                    max_pages,
                    delay,
                )
                self.send_json(
                    {
                        "ok": True,
                        "started": started,
                        "reason": reason,
                        "retry_after": retry_after,
                    },
                    HTTPStatus.ACCEPTED,
                )
                return

            if not backend.start_sync_thread(start_page, max_pages, delay, mode):
                self.send_json(
                    {"error": "A sync is already running."},
                    HTTPStatus.CONFLICT,
                )
                return
            self.send_json({"ok": True}, HTTPStatus.ACCEPTED)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def send_json(
            self,
            payload: Any,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[{self.log_date_time_string()}] {format % args}")

    return AppHandler
