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
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "jnovels_cache.db"
BASE_URL = "https://jnovels.com"
USER_AGENT = "JNovelsCache/1.0 (+local personal metadata cache)"
DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100
AUTO_REPAIR_LIMIT = 20
AUTO_REPAIR_MAX_ATTEMPTS = 3
AUTO_SERIES_REFRESH_LIMIT = 5
SERIES_STALE_DAYS = 30
SYNC_CHECKPOINT_KEY = "active_sync"
EXCLUDED_SEARCH_PATHS = {"/light-novel-epub-jp/"}


def parse_positive_int(value: str, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


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
    release_group: str | None = None


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
        self._list_items: list[dict[str, Any]] = []
        self._release_group: str | None = None
        self._volume_urls: set[str] = set()

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
            self._list_items.append({"parts": [], "download_url": None})

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._content_depth and self._anchor_href:
            self._finish_anchor()
            self._anchor_href = None
            self._anchor_parts = []
        elif tag == "li" and self._content_depth and self._list_items:
            self._finish_list_item(self._list_items.pop())
        elif tag == "div" and self._content_depth:
            self._content_depth -= 1

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split()).casefold()
        if self._content_depth and not self._list_items:
            if re.search(r"\bofficial (?:translation|translated version|version)\b", normalized):
                self._release_group = "Official translation"
            elif re.search(r"\bfan (?:translation|translated version|version)\b", normalized):
                self._release_group = "Fan translation"
        if self._anchor_href:
            self._anchor_parts.append(data)
        if self._list_items:
            self._list_items[-1]["parts"].append(data)

    def _finish_anchor(self) -> None:
        href = urllib.parse.urljoin(self.post_url, self._anchor_href or "")
        text = " ".join("".join(self._anchor_parts).split())
        normalized = text.casefold()

        if re.fullmatch(r"refer to (?:the )?(?:original|main) post", normalized):
            self.original_post_url = href
            return

        if not is_external_http_url(href):
            return
        volume_label = extract_volume_label(text)
        if self._list_items and ("download" in normalized or volume_label):
            self._list_items[-1]["download_url"] = href
        if not self.download_url and (
            "download" in normalized or volume_label
        ):
            self.download_url = href
        if volume_label and not self._list_items:
            self._append_volume(volume_label, href)

    def _finish_list_item(self, item: dict[str, Any]) -> None:
        download_url = item["download_url"]
        if not download_url:
            return
        text = " ".join("".join(item["parts"]).split())
        label = extract_catalog_label(text)
        if not label:
            return
        self._append_volume(label, download_url)

    def _append_volume(self, label: str, download_url: str) -> None:
        if download_url in self._volume_urls:
            return
        self._volume_urls.add(download_url)
        self.volumes.append(
            SeriesVolume(
                label,
                download_url,
                len(self.volumes),
                self._release_group,
            )
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
        if urllib.parse.urlparse(self._url).path in EXCLUDED_SEARCH_PATHS:
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
    return extract_volume_label(value) is not None


def extract_volume_label(value: str) -> str | None:
    match = re.search(
        r"\b(?:(YEAR|PART)\s+([0-9]+)\s+)?"
        r"(?:VOLUME|VOL\.?)\s*([0-9]+(?:\.[0-9]+)?)(\+*)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    number = normalize_volume_number(match.group(3))
    suffix = match.group(4)
    if match.group(1):
        prefix = match.group(1).title()
        prefix_number = normalize_volume_number(match.group(2))
        return f"{prefix} {prefix_number} Volume {number}{suffix}"
    return f"Volume {number}{suffix}"


def extract_named_release_label(value: str) -> str | None:
    match = re.search(
        r"\b(SIDE STORIES|DISC EX|GAIDEN|AFTER STORY)\b",
        value,
        re.IGNORECASE,
    )
    return match.group(1).title() if match else None


def extract_catalog_label(value: str) -> str | None:
    return extract_volume_label(value) or extract_named_release_label(value)


def normalize_volume_number(value: str) -> str:
    whole, separator, fraction = value.partition(".")
    normalized_whole = str(int(whole))
    return f"{normalized_whole}.{fraction}" if separator else normalized_whole


def parse_book_identity(title: str, url: str) -> tuple[str, str | None]:
    cleaned = re.sub(r"\s+Epub$", "", title, flags=re.IGNORECASE).strip()
    match = re.search(
        r"\s+Volume\s+([0-9]+(?:\.[0-9]+)?)$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if match:
        series_title = cleaned[: match.start()].rstrip(" .…")
        return series_title, f"Vol. {normalize_volume_number(match.group(1))}"

    slug_match = re.search(
        r"-volume-([0-9]+(?:\.[0-9]+)?)-epub/?$",
        urllib.parse.urlparse(url).path,
        flags=re.IGNORECASE,
    )
    if slug_match:
        return (
            cleaned.rstrip(" .…"),
            f"Vol. {normalize_volume_number(slug_match.group(1))}",
        )

    series_title = re.sub(
        r"\s+Light\s+Novel$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).rstrip(" .…")
    return series_title, None


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
                detail_cached_at TEXT,
                detail_attempts INTEGER NOT NULL DEFAULT 0,
                detail_last_attempt_at TEXT,
                detail_last_error TEXT
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
                release_group TEXT,
                UNIQUE(series_url, download_url)
            );

            CREATE INDEX IF NOT EXISTS idx_series_volumes_url
            ON series_volumes(series_url, position);

            CREATE TABLE IF NOT EXISTS series_status (
                series_url TEXT PRIMARY KEY,
                checked_at TEXT NOT NULL
            );
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
            "detail_attempts": "ALTER TABLE books ADD COLUMN detail_attempts INTEGER NOT NULL DEFAULT 0",
            "detail_last_attempt_at": "ALTER TABLE books ADD COLUMN detail_last_attempt_at TEXT",
            "detail_last_error": "ALTER TABLE books ADD COLUMN detail_last_error TEXT",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)
        volume_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(series_volumes)").fetchall()
        }
        if "release_group" not in volume_columns:
            connection.execute(
                "ALTER TABLE series_volumes ADD COLUMN release_group TEXT"
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO series_status (series_url, checked_at)
            SELECT series_url, MAX(cached_at)
            FROM series_volumes
            GROUP BY series_url
            """
        )


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


def delete_metadata(key: str) -> None:
    with connect_db() as connection:
        connection.execute("DELETE FROM metadata WHERE key = ?", (key,))


def get_metadata() -> dict[str, str]:
    with connect_db() as connection:
        rows = connection.execute("SELECT key, value FROM metadata").fetchall()
    return {row["key"]: row["value"] for row in rows}


def get_public_metadata() -> dict[str, str]:
    metadata = get_metadata()
    metadata.pop(SYNC_CHECKPOINT_KEY, None)
    return metadata


def save_sync_checkpoint(checkpoint: dict[str, Any]) -> None:
    set_metadata(SYNC_CHECKPOINT_KEY, json.dumps(checkpoint, ensure_ascii=False))


def load_sync_checkpoint() -> dict[str, Any] | None:
    raw = get_metadata().get(SYNC_CHECKPOINT_KEY)
    if not raw:
        return None
    try:
        checkpoint = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        delete_metadata(SYNC_CHECKPOINT_KEY)
        return None
    return checkpoint if isinstance(checkpoint, dict) else None


def clear_sync_checkpoint() -> None:
    delete_metadata(SYNC_CHECKPOINT_KEY)


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
                detail_cached_at = ?,
                detail_attempts = 0,
                detail_last_attempt_at = ?,
                detail_last_error = NULL
            WHERE url = ?
            """,
            (
                details.entry_type,
                details.original_post_url,
                details.download_url,
                now,
                now,
                url,
            ),
        )
    if details.entry_type == "series":
        replace_series_volumes(url, details.volumes)
        reconcile_series_downloads(url)


def validate_post_details(details: PostDetails) -> None:
    if not details.download_url:
        raise ValueError("No download link found on the post.")


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
                (series_url, label, download_url, position, cached_at, release_group)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    series_url,
                    volume.label,
                    volume.download_url,
                    volume.position,
                    now,
                    volume.release_group,
                )
                for volume in volumes
            ],
        )
        connection.execute(
            """
            INSERT INTO series_status (series_url, checked_at)
            VALUES (?, ?)
            ON CONFLICT(series_url) DO UPDATE SET checked_at = excluded.checked_at
            """,
            (series_url, now),
        )


def reconcile_series_downloads(series_url: str) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        volume_rows = connection.execute(
            """
            SELECT label, download_url
            FROM series_volumes
            WHERE series_url = ?
            ORDER BY position
            """,
            (series_url,),
        ).fetchall()
        downloads_by_label = {
            row["label"].casefold(): row["download_url"] for row in volume_rows
        }
        unresolved = connection.execute(
            """
            SELECT url, title
            FROM books
            WHERE original_post_url = ?
              AND download_url IS NULL
            """,
            (series_url,),
        ).fetchall()
        updates = []
        for row in unresolved:
            label = extract_catalog_label(row["title"])
            download_url = downloads_by_label.get((label or "").casefold())
            if download_url:
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
    return len(updates)


def get_cached_urls() -> set[str]:
    with connect_db() as connection:
        rows = connection.execute("SELECT url FROM books").fetchall()
    return {row["url"] for row in rows}


def record_detail_failure(url: str, error: Exception) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            """
            UPDATE books
            SET detail_attempts = detail_attempts + 1,
                detail_last_attempt_at = ?,
                detail_last_error = ?
            WHERE url = ?
            """,
            (now, str(error)[:500], url),
        )


def get_incomplete_detail_urls(
    limit: int,
    *,
    max_attempts: int | None = None,
    exclude_urls: set[str] | None = None,
) -> list[str]:
    conditions = [
        """
        (
            b.detail_cached_at IS NULL
            OR b.entry_type IS NULL
            OR b.download_url IS NULL
        )
        """
    ]
    params: list[Any] = []
    if max_attempts is not None:
        conditions.append("b.detail_attempts < ?")
        params.append(max_attempts)
    if exclude_urls:
        placeholders = ", ".join("?" for _ in exclude_urls)
        conditions.append(f"b.url NOT IN ({placeholders})")
        params.extend(sorted(exclude_urls))
    params.append(limit)

    with connect_db() as connection:
        rows = connection.execute(
            f"""
            SELECT b.url
            FROM books AS b
            WHERE {" AND ".join(conditions)}
            ORDER BY b.detail_last_attempt_at IS NOT NULL,
                     b.detail_last_attempt_at,
                     b.published_at DESC,
                     b.id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [row["url"] for row in rows]


def get_cache_health() -> dict[str, int]:
    with connect_db() as connection:
        pending = connection.execute(
            """
            SELECT COUNT(*)
            FROM books AS b
            WHERE b.detail_cached_at IS NULL
               OR b.entry_type IS NULL
               OR b.download_url IS NULL
            """
        ).fetchone()[0]
        paused = connection.execute(
            """
            SELECT COUNT(*)
            FROM books AS b
            WHERE (
                    b.detail_cached_at IS NULL
                    OR b.entry_type IS NULL
                    OR b.download_url IS NULL
                  )
              AND b.detail_attempts >= ?
            """,
            (AUTO_REPAIR_MAX_ATTEMPTS,),
        ).fetchone()[0]
        failed = connection.execute(
            """
            SELECT COUNT(*)
            FROM books
            WHERE detail_attempts > 0
              AND detail_last_error IS NOT NULL
            """
        ).fetchone()[0]
        stale_series = count_stale_series(connection=connection)
    return {
        "pending_details": pending,
        "failed_retries": failed,
        "paused_retries": paused,
        "stale_series": stale_series,
    }


def series_stale_cutoff() -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=SERIES_STALE_DAYS)
    ).isoformat(timespec="seconds")


def stale_series_where_clause() -> str:
    return """
        (
            ss.checked_at < ?
            OR EXISTS (
                SELECT 1
                FROM books AS b
                WHERE b.original_post_url = ss.series_url
                  AND b.cached_at > ss.checked_at
            )
            OR EXISTS (
                SELECT 1
                FROM series_volumes AS sv
                WHERE sv.series_url = ss.series_url
                GROUP BY sv.series_url
                HAVING COUNT(*) > COUNT(DISTINCT sv.label)
                   AND COUNT(sv.release_group) = 0
            )
        )
    """


def count_stale_series(*, connection: sqlite3.Connection | None = None) -> int:
    def count(active_connection: sqlite3.Connection) -> int:
        return active_connection.execute(
            f"""
            SELECT COUNT(*)
            FROM series_status AS ss
            WHERE {stale_series_where_clause()}
            """,
            (series_stale_cutoff(),),
        ).fetchone()[0]

    if connection is not None:
        return count(connection)
    with connect_db() as active_connection:
        return count(active_connection)


def get_stale_series_urls(limit: int) -> list[str]:
    with connect_db() as connection:
        rows = connection.execute(
            f"""
            SELECT ss.series_url
            FROM series_status AS ss
            WHERE {stale_series_where_clause()}
            ORDER BY ss.checked_at, ss.series_url
            LIMIT ?
            """,
            (series_stale_cutoff(), limit),
        ).fetchall()
    return [row["series_url"] for row in rows]


def list_repair_failures(limit: int = 100) -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT title, url, detail_attempts, detail_last_attempt_at, detail_last_error
            FROM books
            WHERE detail_attempts > 0
              AND detail_last_error IS NOT NULL
            ORDER BY detail_last_attempt_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def is_cached_book_url(url: str) -> bool:
    with connect_db() as connection:
        return connection.execute(
            "SELECT 1 FROM books WHERE url = ?",
            (url,),
        ).fetchone() is not None


sync_lock = threading.Lock()
sync_state: dict[str, Any] = {
    "running": False,
    "mode": None,
    "phase": None,
    "page": 0,
    "pages_requested": 0,
    "pages_completed": 0,
    "items_seen": 0,
    "new_items": 0,
    "details_completed": 0,
    "details_total": 0,
    "detail_errors": 0,
    "series_completed": 0,
    "series_total": 0,
    "series_errors": 0,
    "error": None,
    "notice": None,
    "started_at": None,
    "finished_at": None,
}


def collect_sync_books(
    mode: str,
    start_page: int,
    max_pages: int,
    delay: float,
    known_urls: set[str],
    on_page: Callable[[int, list[Book]], None] | None = None,
) -> list[Book]:
    collected: list[Book] = []
    fetched_pages: dict[int, list[Book]] = {}

    def fetch(page: int) -> list[Book]:
        if page not in fetched_pages:
            sync_state["page"] = page
            fetched_pages[page] = fetch_page(page)
            sync_state["pages_completed"] += 1
            sync_state["items_seen"] += len(fetched_pages[page])
            if on_page:
                on_page(page, fetched_pages[page])
        return fetched_pages[page]

    def add_new(books: list[Book]) -> int:
        new_books = [book for book in books if book.url not in known_urls]
        collected.extend(new_books)
        known_urls.update(book.url for book in new_books)
        sync_state["new_items"] += len(new_books)
        return len(new_books)

    if mode == "catchup":
        for page in range(1, max_pages + 1):
            books = fetch(page)
            if not books:
                break
            page_had_known = any(book.url in known_urls for book in books)
            add_new(books)
            if page_had_known:
                break
            if page < max_pages:
                time.sleep(delay)
        return collected

    if mode == "extend":
        if not known_urls:
            for page in range(1, max_pages + 1):
                books = fetch(page)
                if not books:
                    break
                add_new(books)
                if page < max_pages:
                    time.sleep(delay)
            return collected

        first_known_page = 1
        page_size = 0
        for page in range(1, min(max_pages, 10) + 1):
            books = fetch(page)
            if not books:
                return collected
            page_size = max(page_size, len(books))
            page_had_known = any(book.url in known_urls for book in books)
            add_new(books)
            if page_had_known:
                first_known_page = page
                break
            time.sleep(delay)

        if page_size == 0:
            return collected

        estimated_frontier = max(
            first_known_page + 1,
            (len(known_urls) + page_size - 1) // page_size,
        )
        candidate_page = max(first_known_page + 1, estimated_frontier - 2)
        candidate_books = fetch(candidate_page)
        while (
            candidate_page > first_known_page + 1
            and not any(book.url in known_urls for book in candidate_books)
        ):
            candidate_page -= 1
            candidate_books = fetch(candidate_page)

        page = (
            candidate_page + 1
            if any(book.url in known_urls for book in candidate_books)
            else first_known_page + 1
        )
        uncached_pages_added = 0
        while uncached_pages_added < max_pages:
            books = fetch(page)
            if not books:
                break
            new_count = add_new(books)
            if new_count:
                uncached_pages_added += 1
            page += 1
            if uncached_pages_added < max_pages:
                time.sleep(delay)
        return collected

    for offset in range(max_pages):
        page = start_page + offset
        books = fetch(page)
        if not books:
            break
        collected.extend(books)
        sync_state["new_items"] += sum(book.url not in known_urls for book in books)
        known_urls.update(book.url for book in books)
        if offset < max_pages - 1:
            time.sleep(delay)
    return collected


def run_sync(
    start_page: int,
    max_pages: int,
    delay: float,
    mode: str = "manual",
    detail_urls_override: list[str] | None = None,
    series_urls_override: list[str] | None = None,
    recovery: bool = False,
) -> None:
    if not sync_lock.acquire(blocking=False):
        return

    try:
        sync_state.update(
            {
                "running": True,
                "mode": mode,
                "phase": "search",
                "page": start_page,
                "pages_requested": max_pages,
                "pages_completed": 0,
                "items_seen": 0,
                "new_items": 0,
                "details_completed": 0,
                "details_total": 0,
                "detail_errors": 0,
                "series_completed": 0,
                "series_total": 0,
                "series_errors": 0,
                "error": None,
                "notice": None,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "finished_at": None,
            }
        )
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
        save_sync_checkpoint(checkpoint)

        scheduled_series_urls: set[str] = set(series_urls_override or [])
        if detail_urls_override is not None:
            books = []
            detail_urls = list(dict.fromkeys(detail_urls_override))
        elif mode == "series_refresh":
            books = []
            detail_urls = []
            scheduled_series_urls.update(get_stale_series_urls(max_pages))
        elif mode == "repair":
            books: list[Book] = []
            detail_urls = get_incomplete_detail_urls(max_pages)
        else:
            known_urls = get_cached_urls()

            def persist_page(page: int, page_books: list[Book]) -> None:
                upsert_books(page_books)
                pending = checkpoint["pending_detail_urls"]
                pending.extend(book.url for book in page_books if book.url not in pending)
                checkpoint["page"] = page
                checkpoint["pages_completed"] = sync_state["pages_completed"]
                save_sync_checkpoint(checkpoint)

            books = collect_sync_books(
                mode,
                start_page,
                max_pages,
                delay,
                known_urls,
                persist_page,
            )
            detail_urls = list(dict.fromkeys(book.url for book in books))
            detail_urls.extend(
                get_incomplete_detail_urls(
                    AUTO_REPAIR_LIMIT,
                    max_attempts=AUTO_REPAIR_MAX_ATTEMPTS,
                    exclude_urls=set(detail_urls),
                )
            )
            scheduled_series_urls.update(
                get_stale_series_urls(AUTO_SERIES_REFRESH_LIMIT)
            )
        sync_state["phase"] = "details"
        sync_state["details_total"] = len(detail_urls)
        checkpoint["phase"] = "details"
        checkpoint["pending_detail_urls"] = list(dict.fromkeys(detail_urls))
        checkpoint["pending_series_urls"] = sorted(scheduled_series_urls)
        save_sync_checkpoint(checkpoint)
        discovered_series_urls: set[str] = set()
        for index, url in enumerate(detail_urls):
            time.sleep(delay)
            completed = False
            try:
                details = fetch_post_details(url)
                validate_post_details(details)
                update_book_details(url, details)
                if details.entry_type == "series":
                    discovered_series_urls.add(url)
                elif details.original_post_url:
                    discovered_series_urls.add(details.original_post_url)
                completed = True
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                record_detail_failure(url, exc)
                sync_state["detail_errors"] += 1
                completed = True
            finally:
                if completed:
                    sync_state["details_completed"] += 1
                    checkpoint["pending_detail_urls"] = detail_urls[index + 1 :]
                    checkpoint["pending_series_urls"] = sorted(
                        scheduled_series_urls | discovered_series_urls
                    )
                    save_sync_checkpoint(checkpoint)

        missing_series_urls = sorted(
            (discovered_series_urls | scheduled_series_urls) - set(detail_urls)
        )
        sync_state["phase"] = "series"
        sync_state["series_total"] = len(missing_series_urls)
        checkpoint["phase"] = "series"
        checkpoint["pending_detail_urls"] = []
        checkpoint["pending_series_urls"] = missing_series_urls
        save_sync_checkpoint(checkpoint)
        for index, series_url in enumerate(missing_series_urls):
            time.sleep(delay)
            completed = False
            try:
                details = fetch_post_details(series_url)
                validate_post_details(details)
                update_book_details(series_url, details)
                completed = True
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                record_detail_failure(series_url, exc)
                sync_state["series_errors"] += 1
                completed = True
            finally:
                if completed:
                    sync_state["series_completed"] += 1
                    checkpoint["pending_series_urls"] = missing_series_urls[index + 1 :]
                    save_sync_checkpoint(checkpoint)

        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sync_state["finished_at"] = finished
        sync_state["phase"] = None
        if recovery:
            sync_state["notice"] = (
                "Recovered saved work from an interrupted sync; "
                "the original search may have stopped early."
            )
            set_metadata("last_sync_recovered_at", finished)
        else:
            set_metadata("last_sync_at", finished)
            set_metadata("last_sync_pages", str(sync_state["pages_completed"]))
            set_metadata("last_detail_errors", str(sync_state["detail_errors"]))
            set_metadata("last_series_errors", str(sync_state["series_errors"]))
            delete_metadata("last_sync_interrupted_at")
        clear_sync_checkpoint()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        sync_state["error"] = str(exc)
        sync_state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sync_state["phase"] = None
    finally:
        sync_state["running"] = False
        sync_lock.release()


def recover_interrupted_sync(checkpoint: dict[str, Any] | None = None) -> bool:
    checkpoint = checkpoint or load_sync_checkpoint()
    if not checkpoint or checkpoint.get("status") != "active":
        return False

    interrupted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    set_metadata("last_sync_interrupted_at", interrupted_at)
    detail_urls = [
        str(url) for url in checkpoint.get("pending_detail_urls", []) if url
    ]
    series_urls = [
        str(url) for url in checkpoint.get("pending_series_urls", []) if url
    ]
    if not detail_urls and not series_urls:
        clear_sync_checkpoint()
        sync_state["notice"] = (
            "The previous sync stopped before more work was queued; "
            "completed search pages were saved."
        )
        return False

    run_sync(
        1,
        max(len(detail_urls), 1),
        min(max(float(checkpoint.get("delay", 1.0)), 0.5), 10.0),
        "recovery",
        detail_urls,
        series_urls,
        True,
    )
    return True


def start_interrupted_sync_recovery() -> bool:
    checkpoint = load_sync_checkpoint()
    if not checkpoint or checkpoint.get("status") != "active":
        return False
    thread = threading.Thread(
        target=recover_interrupted_sync,
        args=(checkpoint,),
        name="jnovels-sync-recovery",
        daemon=True,
    )
    thread.start()
    return True


def book_filter(query: str, entry_filter: str = "all") -> tuple[str, tuple[str, ...]]:
    conditions: list[str] = []
    params: list[str] = []
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("title LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")

    if entry_filter == "series":
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


def count_books(query: str, entry_filter: str = "all") -> int:
    where_clause, params = book_filter(query, entry_filter)
    with connect_db() as connection:
        return connection.execute(
            f"SELECT COUNT(*) FROM books{where_clause}",
            params,
        ).fetchone()[0]


def list_books(
    query: str,
    sort: str,
    limit: int | None = None,
    offset: int = 0,
    entry_filter: str = "all",
) -> list[dict[str, Any]]:
    order_by = {
        "published_desc": "published_at IS NULL, published_at DESC, title COLLATE NOCASE, id DESC",
        "published_asc": "published_at IS NULL, published_at ASC, title COLLATE NOCASE, id ASC",
        "title_asc": "title COLLATE NOCASE ASC, id ASC",
        "title_desc": "title COLLATE NOCASE DESC, id DESC",
        "cached_desc": "cached_at DESC, title COLLATE NOCASE, id DESC",
    }.get(sort, "published_at IS NULL, published_at DESC, title COLLATE NOCASE, id DESC")

    where_clause, filter_params = book_filter(query, entry_filter)
    sql = """
        SELECT id, title, url, image_url, published_at, cached_at, last_seen_at,
               entry_type, original_post_url, download_url, detail_cached_at
        FROM books
    """ + where_clause
    params: tuple[Any, ...] = filter_params
    sql += f" ORDER BY {order_by}"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += (limit, offset)

    with connect_db() as connection:
        rows = connection.execute(sql, params).fetchall()

    books = [dict(row) for row in rows]
    series_urls: set[str] = set()
    for book in books:
        series_url = (
            book["original_post_url"]
            if book["entry_type"] == "volume"
            else book["url"] if book["entry_type"] == "series" else None
        )
        if series_url:
            series_urls.add(series_url)
    volume_counts: dict[str, int] = {}
    if series_urls:
        placeholders = ", ".join("?" for _ in series_urls)
        with connect_db() as connection:
            volume_rows = connection.execute(
                f"""
                SELECT series_url, COUNT(*) AS volume_count
                FROM series_volumes
                WHERE series_url IN ({placeholders})
                GROUP BY series_url
                """,
                tuple(series_urls),
            ).fetchall()
        volume_counts = {row["series_url"]: row["volume_count"] for row in volume_rows}

    for book in books:
        series_title, volume_label = parse_book_identity(book["title"], book["url"])
        series_url = (
            book["original_post_url"]
            if book["entry_type"] == "volume"
            else book["url"] if book["entry_type"] == "series" else None
        )
        book["series_title"] = series_title
        book["volume_label"] = volume_label
        book["series_url"] = series_url
        book["volume_count"] = volume_counts.get(series_url or "", 0)
    return books


def list_series_volumes(series_url: str) -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT sv.label,
                   sv.download_url,
                   sv.position,
                   sv.cached_at,
                   sv.release_group,
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
            entry_filter = params.get("filter", ["all"])[0].strip().casefold()
            if entry_filter not in {"all", "series", "volumes", "pending"}:
                entry_filter = "all"
            page = parse_positive_int(params.get("page", ["1"])[0], 1)
            page_size = min(
                parse_positive_int(
                    params.get("page_size", [str(DEFAULT_PAGE_SIZE)])[0],
                    DEFAULT_PAGE_SIZE,
                ),
                MAX_PAGE_SIZE,
            )
            count = count_books(query, entry_filter)
            pages = max(1, (count + page_size - 1) // page_size)
            page = min(page, pages)
            books = list_books(
                query,
                sort,
                page_size,
                (page - 1) * page_size,
                entry_filter,
            )
            self.send_json(
                {
                    "books": books,
                    "count": count,
                    "page": page,
                    "page_size": page_size,
                    "pages": pages,
                    "filter": entry_filter,
                }
            )
            return
        if parsed.path == "/api/status":
            with connect_db() as connection:
                count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            self.send_json(
                {
                    "sync": dict(sync_state),
                    "count": count,
                    "metadata": get_public_metadata(),
                    "health": get_cache_health(),
                }
            )
            return
        if parsed.path == "/api/repair-failures":
            failures = list_repair_failures()
            self.send_json({"failures": failures, "count": len(failures)})
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
        if parsed.path not in {"/api/sync", "/api/repair-entry"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if sync_state["running"]:
            self.send_json({"error": "A sync is already running."}, HTTPStatus.CONFLICT)
            return

        try:
            payload = self.read_json()
            if parsed.path == "/api/repair-entry":
                url = str(payload.get("url", "")).strip()
                delay = min(max(float(payload.get("delay", 1.0)), 0.5), 10.0)
                if not url or not is_cached_book_url(url):
                    self.send_json(
                        {"error": "Cached entry not found."},
                        HTTPStatus.NOT_FOUND,
                    )
                    return
                thread = threading.Thread(
                    target=run_sync,
                    args=(1, 1, delay, "repair", [url]),
                    name="jnovels-repair-entry",
                    daemon=True,
                )
                thread.start()
                self.send_json({"ok": True}, HTTPStatus.ACCEPTED)
                return

            mode = str(payload.get("mode", "catchup")).strip().casefold()
            if mode not in {
                "catchup",
                "extend",
                "manual",
                "repair",
                "series_refresh",
            }:
                raise ValueError("Unknown sync mode")
            start_page = min(max(int(payload.get("start_page", 1)), 1), 10000)
            max_limit = 2000 if mode in {"repair", "series_refresh"} else 200
            max_pages = min(max(int(payload.get("max_pages", 20)), 1), max_limit)
            delay = min(max(float(payload.get("delay", 1.0)), 0.5), 10.0)
        except (ValueError, TypeError, json.JSONDecodeError):
            self.send_json({"error": "Invalid sync options."}, HTTPStatus.BAD_REQUEST)
            return

        thread = threading.Thread(
            target=run_sync,
            args=(start_page, max_pages, delay, mode),
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
    start_interrupted_sync_recovery()
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
