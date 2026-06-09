from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "jnovels_cache.db"
BASE_URL = "https://jnovels.com"
USER_AGENT = "JNovelsCache/1.0 (+local personal metadata cache)"


@dataclass
class Book:
    title: str
    url: str
    image_url: str | None
    published_at: str | None
    entry_type: str | None = None
    original_post_url: str | None = None
    download_url: str | None = None
    detail_cached_at: str | None = None


@dataclass
class PostDetails:
    entry_type: str
    original_post_url: str | None
    download_url: str | None
    volumes: list["SeriesVolume"] = field(default_factory=list)


@dataclass
class SeriesVolume:
    label: str
    download_url: str
    position: int


class PostDetailsParser(HTMLParser):
    def __init__(self, post_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.post_url = post_url
        self.original_post_url: str | None = None
        self.download_url: str | None = None
        self.volumes: list[SeriesVolume] = []
        self._content_depth = 0
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []
        self._list_item_depth = 0
        self._list_item_parts: list[str] = []
        self._list_item_download_url: str | None = None

    @staticmethod
    def _classes(attrs: dict[str, str | None]) -> set[str]:
        return set((attrs.get("class") or "").split())

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        if tag == "div":
            if self._content_depth:
                self._content_depth += 1
            elif "post-content" in self._classes(attrs):
                self._content_depth = 1
            return

        if tag == "a" and self._content_depth:
            self._anchor_href = attrs.get("href")
            self._anchor_parts = []
        elif tag == "li" and self._content_depth:
            self._list_item_depth += 1
            if self._list_item_depth == 1:
                self._list_item_parts = []
                self._list_item_download_url = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._content_depth and self._anchor_href:
            self._finish_anchor()
            self._anchor_href = None
            self._anchor_parts = []
        elif tag == "li" and self._content_depth and self._list_item_depth:
            if self._list_item_depth == 1:
                self._finish_list_item()
            self._list_item_depth -= 1
        elif tag == "div" and self._content_depth:
            self._content_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_parts.append(data)
        if self._list_item_depth:
            self._list_item_parts.append(data)

    def _finish_anchor(self) -> None:
        href = urllib.parse.urljoin(self.post_url, self._anchor_href or "")
        text = " ".join("".join(self._anchor_parts).split())
        normalized = text.casefold()

        if normalized == "refer to original post":
            self.original_post_url = href
            return

        if not is_external_http_url(href):
            return
        if self._list_item_depth and "download" in normalized:
            self._list_item_download_url = href
        if not self.download_url and (
            "download" in normalized or is_volume_label(normalized)
        ):
            self.download_url = href

    def _finish_list_item(self) -> None:
        if not self._list_item_download_url:
            return
        text = " ".join("".join(self._list_item_parts).split())
        match = re.search(r"\bVOLUME\s+([0-9]+(?:\.[0-9]+)?)\b", text, re.IGNORECASE)
        if not match:
            return
        number = match.group(1)
        label = f"Volume {number}"
        self.volumes.append(
            SeriesVolume(label, self._list_item_download_url, len(self.volumes))
        )

    def details(self) -> PostDetails:
        entry_type = "volume" if self.original_post_url else "series"
        return PostDetails(
            entry_type,
            self.original_post_url,
            self.download_url,
            self.volumes if entry_type == "series" else [],
        )


class SearchResultsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.books: list[Book] = []
        self._article_depth = 0
        self._in_title = False
        self._in_date = False
        self._in_featured = False
        self._title_parts: list[str] = []
        self._url: str | None = None
        self._image_url: str | None = None
        self._date_parts: list[str] = []

    @staticmethod
    def _classes(attrs: dict[str, str | None]) -> set[str]:
        return set((attrs.get("class") or "").split())

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        classes = self._classes(attrs)

        if tag == "article":
            if self._article_depth == 0:
                self._reset_article()
            self._article_depth += 1
            return

        if self._article_depth == 0:
            return

        if tag == "div" and "featured-media" in classes:
            self._in_featured = True
        elif tag in {"h1", "h2"} and "post-title" in classes:
            self._in_title = True
        elif tag == "a" and self._in_title and not self._url:
            self._url = attrs.get("href")
        elif tag == "a" and "post-date" in classes:
            self._in_date = True
        elif tag == "img" and self._in_featured and not self._image_url:
            self._image_url = attrs.get("src") or attrs.get("data-src")

    def handle_endtag(self, tag: str) -> None:
        if self._article_depth == 0:
            return

        if tag in {"h1", "h2"} and self._in_title:
            self._in_title = False
        elif tag == "a" and self._in_date:
            self._in_date = False
        elif tag == "div" and self._in_featured:
            self._in_featured = False
        elif tag == "article":
            self._article_depth -= 1
            if self._article_depth == 0:
                self._finish_article()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_date:
            self._date_parts.append(data)

    def _reset_article(self) -> None:
        self._in_title = False
        self._in_date = False
        self._in_featured = False
        self._title_parts = []
        self._url = None
        self._image_url = None
        self._date_parts = []

    def _finish_article(self) -> None:
        title = " ".join("".join(self._title_parts).split())
        if not title or not self._url:
            return

        raw_date = " ".join("".join(self._date_parts).split())
        self.books.append(
            Book(
                title=html.unescape(title),
                url=self._url,
                image_url=self._image_url,
                published_at=normalize_date(raw_date),
            )
        )


def normalize_date(value: str) -> str | None:
    if not value:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return value


def is_external_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() not in {
        "jnovels.com",
        "www.jnovels.com",
    }


def is_volume_label(value: str) -> bool:
    parts = value.strip().split()
    return len(parts) >= 2 and parts[0] == "volume" and parts[1].isdigit()


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_db() -> None:
    with connect_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                image_url TEXT,
                published_at TEXT,
                cached_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                entry_type TEXT,
                original_post_url TEXT,
                download_url TEXT,
                detail_cached_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
            CREATE INDEX IF NOT EXISTS idx_books_published ON books(published_at);

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS series_volumes (
                id INTEGER PRIMARY KEY,
                series_url TEXT NOT NULL,
                label TEXT NOT NULL,
                download_url TEXT NOT NULL,
                position INTEGER NOT NULL,
                cached_at TEXT NOT NULL,
                UNIQUE(series_url, download_url)
            );

            CREATE INDEX IF NOT EXISTS idx_series_volumes_url
            ON series_volumes(series_url, position);
            """
        )
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(books)").fetchall()
        }
        migrations = {
            "entry_type": "ALTER TABLE books ADD COLUMN entry_type TEXT",
            "original_post_url": "ALTER TABLE books ADD COLUMN original_post_url TEXT",
            "download_url": "ALTER TABLE books ADD COLUMN download_url TEXT",
            "detail_cached_at": "ALTER TABLE books ADD COLUMN detail_cached_at TEXT",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)


def upsert_books(books: list[Book]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        before = connection.total_changes
        connection.executemany(
            """
            INSERT INTO books (title, url, image_url, published_at, cached_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                image_url = excluded.image_url,
                published_at = excluded.published_at,
                last_seen_at = excluded.last_seen_at
            """,
            [
                (book.title, book.url, book.image_url, book.published_at, now, now)
                for book in books
            ],
        )
        return connection.total_changes - before


def set_metadata(key: str, value: str) -> None:
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_metadata() -> dict[str, str]:
    with connect_db() as connection:
        rows = connection.execute("SELECT key, value FROM metadata").fetchall()
    return {row["key"]: row["value"] for row in rows}


def search_url(page: int) -> str:
    if page <= 1:
        return f"{BASE_URL}/?s=epub"
    return f"{BASE_URL}/page/{page}/?s=epub"


def fetch_page(page: int, timeout: int = 30) -> list[Book]:
    request = urllib.request.Request(
        search_url(page),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")

    parser = SearchResultsParser()
    parser.feed(body)
    return parser.books


def fetch_post_details(url: str, timeout: int = 30) -> PostDetails:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")

    parser = PostDetailsParser(url)
    parser.feed(body)
    return parser.details()


def update_book_details(url: str, details: PostDetails) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            """
            UPDATE books
            SET entry_type = ?,
                original_post_url = ?,
                download_url = ?,
                detail_cached_at = ?
            WHERE url = ?
            """,
            (
                details.entry_type,
                details.original_post_url,
                details.download_url,
                now,
                url,
            ),
        )
    if details.entry_type == "series":
        replace_series_volumes(url, details.volumes)


def replace_series_volumes(series_url: str, volumes: list[SeriesVolume]) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            "DELETE FROM series_volumes WHERE series_url = ?",
            (series_url,),
        )
        connection.executemany(
            """
            INSERT INTO series_volumes
                (series_url, label, download_url, position, cached_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    series_url,
                    volume.label,
                    volume.download_url,
                    volume.position,
                    now,
                )
                for volume in volumes
            ],
        )


sync_lock = threading.Lock()
sync_state: dict[str, Any] = {
    "running": False,
    "phase": None,
    "page": 0,
    "pages_requested": 0,
    "pages_completed": 0,
    "items_seen": 0,
    "details_completed": 0,
    "details_total": 0,
    "detail_errors": 0,
    "series_completed": 0,
    "series_total": 0,
    "series_errors": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
}


def run_sync(start_page: int, max_pages: int, delay: float) -> None:
    if not sync_lock.acquire(blocking=False):
        return

    try:
        sync_state.update(
            {
                "running": True,
                "phase": "search",
                "page": start_page,
                "pages_requested": max_pages,
                "pages_completed": 0,
                "items_seen": 0,
                "details_completed": 0,
                "details_total": 0,
                "detail_errors": 0,
                "series_completed": 0,
                "series_total": 0,
                "series_errors": 0,
                "error": None,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "finished_at": None,
            }
        )

        detail_urls: list[str] = []
        for offset in range(max_pages):
            page = start_page + offset
            sync_state["page"] = page
            books = fetch_page(page)
            if not books:
                break
            upsert_books(books)
            detail_urls.extend(book.url for book in books)
            sync_state["pages_completed"] += 1
            sync_state["items_seen"] += len(books)
            if offset < max_pages - 1:
                time.sleep(delay)

        detail_urls = list(dict.fromkeys(detail_urls))
        sync_state["phase"] = "details"
        sync_state["details_total"] = len(detail_urls)
        discovered_series_urls: set[str] = set()
        for url in detail_urls:
            time.sleep(delay)
            try:
                details = fetch_post_details(url)
                update_book_details(url, details)
                if details.entry_type == "series":
                    discovered_series_urls.add(url)
                elif details.original_post_url:
                    discovered_series_urls.add(details.original_post_url)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                sync_state["detail_errors"] += 1
            finally:
                sync_state["details_completed"] += 1

        missing_series_urls = sorted(discovered_series_urls - set(detail_urls))
        sync_state["phase"] = "series"
        sync_state["series_total"] = len(missing_series_urls)
        for series_url in missing_series_urls:
            time.sleep(delay)
            try:
                details = fetch_post_details(series_url)
                replace_series_volumes(series_url, details.volumes)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                sync_state["series_errors"] += 1
            finally:
                sync_state["series_completed"] += 1

        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sync_state["finished_at"] = finished
        sync_state["phase"] = None
        set_metadata("last_sync_at", finished)
        set_metadata("last_sync_pages", str(sync_state["pages_completed"]))
        set_metadata("last_detail_errors", str(sync_state["detail_errors"]))
        set_metadata("last_series_errors", str(sync_state["series_errors"]))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        sync_state["error"] = str(exc)
        sync_state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sync_state["phase"] = None
    finally:
        sync_state["running"] = False
        sync_lock.release()


def list_books(query: str, sort: str) -> list[dict[str, Any]]:
    order_by = {
        "published_desc": "published_at IS NULL, published_at DESC, title COLLATE NOCASE",
        "published_asc": "published_at IS NULL, published_at ASC, title COLLATE NOCASE",
        "title_asc": "title COLLATE NOCASE ASC",
        "title_desc": "title COLLATE NOCASE DESC",
        "cached_desc": "cached_at DESC, title COLLATE NOCASE",
    }.get(sort, "published_at IS NULL, published_at DESC, title COLLATE NOCASE")

    sql = """
        SELECT id, title, url, image_url, published_at, cached_at, last_seen_at,
               entry_type, original_post_url, download_url, detail_cached_at
        FROM books
    """
    params: tuple[str, ...] = ()
    if query:
        sql += " WHERE title LIKE ? ESCAPE '\\'"
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params = (f"%{escaped}%",)
    sql += f" ORDER BY {order_by}"

    with connect_db() as connection:
        rows = connection.execute(sql, params).fetchall()
        volume_rows = connection.execute(
            """
            SELECT series_url, label, download_url, position, cached_at
            FROM series_volumes
            ORDER BY series_url, position
            """
        ).fetchall()

    volumes_by_series: dict[str, list[dict[str, Any]]] = {}
    for row in volume_rows:
        volume = dict(row)
        series_url = volume.pop("series_url")
        volumes_by_series.setdefault(series_url, []).append(volume)

    books = [dict(row) for row in rows]
    for book in books:
        series_url = (
            book["original_post_url"]
            if book["entry_type"] == "volume"
            else book["url"] if book["entry_type"] == "series" else None
        )
        book["series_url"] = series_url
        book["volume_count"] = len(volumes_by_series.get(series_url or "", []))
    return books


def list_series_volumes(series_url: str) -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT sv.label,
                   sv.download_url,
                   sv.position,
                   sv.cached_at,
                   (
                       SELECT b.published_at
                       FROM books AS b
                       WHERE b.download_url = sv.download_url
                         AND (
                             b.original_post_url = sv.series_url
                             OR b.url = sv.series_url
                         )
                       ORDER BY (b.entry_type = 'volume') DESC
                       LIMIT 1
                   ) AS published_at,
                   (
                       SELECT b.url
                       FROM books AS b
                       WHERE b.download_url = sv.download_url
                         AND (
                             b.original_post_url = sv.series_url
                             OR b.url = sv.series_url
                         )
                       ORDER BY (b.entry_type = 'volume') DESC
                       LIMIT 1
                   ) AS post_url
            FROM series_volumes AS sv
            WHERE sv.series_url = ?
            ORDER BY sv.position
            """,
            (series_url,),
        ).fetchall()
    return [dict(row) for row in rows]


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/books":
            params = urllib.parse.parse_qs(parsed.query)
            query = params.get("q", [""])[0].strip()
            sort = params.get("sort", ["published_desc"])[0]
            books = list_books(query, sort)
            self.send_json({"books": books, "count": len(books)})
            return
        if parsed.path == "/api/status":
            with connect_db() as connection:
                count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            self.send_json(
                {"sync": dict(sync_state), "count": count, "metadata": get_metadata()}
            )
            return
        if parsed.path == "/api/series":
            params = urllib.parse.parse_qs(parsed.query)
            series_url = params.get("url", [""])[0].strip()
            if not series_url:
                self.send_json({"error": "Missing series URL."}, HTTPStatus.BAD_REQUEST)
                return
            volumes = list_series_volumes(series_url)
            self.send_json(
                {
                    "series_url": series_url,
                    "volumes": volumes,
                    "count": len(volumes),
                }
            )
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/sync":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if sync_state["running"]:
            self.send_json({"error": "A sync is already running."}, HTTPStatus.CONFLICT)
            return

        try:
            payload = self.read_json()
            start_page = min(max(int(payload.get("start_page", 1)), 1), 10000)
            max_pages = min(max(int(payload.get("max_pages", 6)), 1), 200)
            delay = min(max(float(payload.get("delay", 1.0)), 0.5), 10.0)
        except (ValueError, TypeError, json.JSONDecodeError):
            self.send_json({"error": "Invalid sync options."}, HTTPStatus.BAD_REQUEST)
            return

        thread = threading.Thread(
            target=run_sync,
            args=(start_page, max_pages, delay),
            name="jnovels-sync",
            daemon=True,
        )
        thread.start()
        self.send_json({"ok": True}, HTTPStatus.ACCEPTED)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local JNovels EPUB search cache")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args()

    initialize_db()
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"JNovels Cache is running at {url}")
    print("Press Ctrl+C to stop.")
    if args.open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
