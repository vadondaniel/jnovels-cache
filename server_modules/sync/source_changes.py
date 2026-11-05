"""Batch source change detection; source checks never advance full-fetch timestamps."""
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

from server_modules.config import BASE_URL, USER_AGENT
from server_modules.storage.metadata_store import get_metadata

API_CURSOR = "series_source_api_cursor"
API_CHECKED = "series_source_api_checked_at"
SITEMAP_CHECKED = "series_source_sitemap_checked_at"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Missing or invalid source timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def source_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid source URL")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" or parts.netloc != urllib.parse.urlsplit(BASE_URL).netloc or parts.query or parts.fragment:
        raise ValueError("Unexpected source URL")
    return value.rstrip("/")


def fetch_source(url: str) -> tuple[bytes, Any]:
    source_url(url.split("?", 1)[0])
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,application/xml,text/xml"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(), response.headers


def api_changes(cursor: str, fetch: Callable, progress: Callable) -> dict[str, str]:
    boundary = parse_timestamp(cursor) - timedelta(days=1)
    changes: dict[str, str] = {}
    previous = None
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page, "orderby": "modified", "order": "desc", "_fields": "id,link,modified_gmt"})
        body, headers = fetch(f"{BASE_URL}/wp-json/wp/v2/posts?{query}")
        rows = json.loads(body)
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("Invalid source API response")
        stop = False
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid source API entry")
            url = source_url(row["link"])
            modified = parse_timestamp(row["modified_gmt"])
            if previous is not None and modified > previous:
                raise ValueError("Source API is not ordered by modification time")
            previous = modified
            if modified < boundary:
                stop = True
            else:
                changes[url] = max(changes.get(url, ""), modified.isoformat(timespec="seconds"))
        progress(len(rows))
        total_pages = int(headers["X-WP-TotalPages"])
        if total_pages < 0 or (rows and total_pages == 0) or page > max(total_pages, 1) or (not rows and page < total_pages):
            raise ValueError("Invalid source API pagination")
        if stop or page >= total_pages:
            return changes
        if not rows:
            raise ValueError("Empty source API page")
        page += 1


def sitemap_changes(fetch: Callable, progress: Callable) -> dict[str, str]:
    body, _ = fetch(f"{BASE_URL}/sitemap_index.xml")
    root = ET.fromstring(body)
    if root.tag != f"{{{NS['s']}}}sitemapindex":
        raise ValueError("Invalid sitemap index")
    urls = []
    for entry in root.findall("s:sitemap", NS):
        url = source_url(entry.findtext("s:loc", default="", namespaces=NS))
        path = urllib.parse.urlsplit(url).path
        if path.startswith("/post-sitemap") and path.endswith(".xml"):
            urls.append(url)
    if not urls:
        raise ValueError("No post sitemaps found")
    changes: dict[str, str] = {}
    for url in dict.fromkeys(urls):
        body, _ = fetch(url)
        root = ET.fromstring(body)
        entries = root.findall("s:url", NS)
        if root.tag != f"{{{NS['s']}}}urlset" or not entries:
            raise ValueError("Invalid or empty post sitemap")
        for entry in entries:
            post = source_url(entry.findtext("s:loc", default="", namespaces=NS))
            raw = entry.findtext("s:lastmod", default="", namespaces=NS)
            # Missing dates cannot establish that content is unchanged.
            if raw:
                modified = parse_timestamp(raw).isoformat(timespec="seconds")
                changes[post] = max(changes.get(post, ""), modified)
        progress(len(entries))
    return changes


def store_changes(connect_db: Callable, changes: dict[str, str], metadata: dict[str, str]) -> int:
    matched = 0
    with connect_db() as connection:
        rows = connection.execute("SELECT series_url, checked_at, source_modified_at FROM series_status WHERE unavailable_at IS NULL").fetchall()
        for row in rows:
            modified = changes.get(row["series_url"].rstrip("/"))
            if modified is None:
                continue
            matched += 1
            latest = max(modified, row["source_modified_at"] or "")
            pending = int(parse_timestamp(latest) > parse_timestamp(row["checked_at"]))
            connection.execute(
                "UPDATE series_status SET source_modified_at = ?, source_change_pending = MAX(source_change_pending, ?) WHERE series_url = ?",
                (latest, pending, row["series_url"]),
            )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            metadata.items(),
        )
    return matched


def check_source_changes(connect_db: Callable, state: dict, delay: float, sleep: Callable, *, force: bool = False, fetch: Callable = fetch_source, now: datetime | None = None) -> None:
    with connect_db() as connection:
        if not connection.execute("SELECT 1 FROM series_status WHERE unavailable_at IS NULL LIMIT 1").fetchone():
            return
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    metadata = get_metadata(connect_db)

    def due(key: str, days: int) -> bool:
        try:
            return parse_timestamp(metadata[key]) <= now - timedelta(days=days)
        except (KeyError, ValueError):
            return True

    def progress(count: int) -> None:
        state["source_entries_checked"] = state.get("source_entries_checked", 0) + count

    def paced_fetch(url: str):
        sleep(delay)
        result = fetch(url)
        state["source_requests_completed"] = state.get("source_requests_completed", 0) + 1
        return result

    def run(label: str, operation: Callable, markers: dict) -> bool:
        state["phase"] = "source"
        state["source_service"] = label
        state["phase_started_at"] = stamp
        try:
            changes = operation()
            state["source_series_checked"] = state.get("source_series_checked", 0) + store_changes(connect_db, changes, markers)
            return True
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, ET.ParseError, sqlite3.Error) as exc:
            state.setdefault("source_warnings", []).append(f"{label} check failed; saved verification state retained: {exc}")
            return False

    if API_CURSOR not in metadata:
        run("Sitemap", lambda: sitemap_changes(paced_fetch, progress), {SITEMAP_CHECKED: stamp, API_CURSOR: stamp, API_CHECKED: stamp})
        return
    if force or due(API_CHECKED, 1):
        run("API", lambda: api_changes(metadata[API_CURSOR], paced_fetch, progress), {API_CURSOR: stamp, API_CHECKED: stamp})
    if due(SITEMAP_CHECKED, 7):
        run("Sitemap", lambda: sitemap_changes(paced_fetch, progress), {SITEMAP_CHECKED: stamp})
