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
AUTO_SYNC_COOLDOWN_SECONDS = 10 * 60
SERIES_STALE_DAYS = 30
SYNC_CHECKPOINT_KEY = "active_sync"
LEGACY_REPARSE_KEY = "legacy_format_reparse_v1"
EXCLUDED_SEARCH_PATHS = {
    "/light-novel-epub-jp/",
    "/top-light-novels-to-read/",
}

_catalog_index_lock = threading.Lock()
_catalog_index_cache: tuple[
    Path,
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
] | None = None


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
    associated_names: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    synopsis: str | None = None
    parent_hints: list[str] = field(default_factory=list)


@dataclass
class SeriesVolume:
    label: str
    download_url: str
    position: int
    release_group: str | None = None
    source_list: int = field(default=0, compare=False, repr=False)


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
        self._paragraph_item: dict[str, Any] | None = None
        self._release_group: str | None = None
        self._volume_urls: set[str] = set()
        self._volume_keys: dict[tuple[int, str, str], int] = {}
        self._list_depth = 0
        self._list_index = 0
        self._current_list_index = 0
        self._description_depth = 0
        self._associated_depth = 0
        self._in_associated_heading = False
        self._description_parts: list[str] = []
        self._associated_parts: list[str] = []
        self._genres: list[str] = []
        self._parent_hints: list[str] = []
        self._metadata_section: str | None = None
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    @staticmethod
    def _classes(attrs: dict[str, str | None]) -> set[str]:
        return set((attrs.get("class") or "").split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = self._classes(attributes)
        if tag == "div":
            if self._description_depth:
                self._description_depth += 1
            elif attributes.get("id") == "editdescription":
                self._description_depth = 1
            if self._associated_depth:
                self._associated_depth += 1
            elif attributes.get("id") == "editassociated":
                self._associated_depth = 1
            if self._content_depth:
                self._content_depth += 1
            elif "post-content" in classes:
                self._content_depth = 1
            return

        if tag in {"h2", "h3", "h4", "h5", "h6"} and self._content_depth:
            self._heading_tag = tag
            self._heading_parts = []
            if "associated" in classes:
                self._in_associated_heading = True
        elif tag == "br" and self._associated_depth:
            self._associated_parts.append("\n")
        elif tag == "br" and self._paragraph_item:
            self._paragraph_item["parts"].append(" ")

        if tag in {"ol", "ul"} and self._content_depth:
            if self._list_depth == 0:
                self._list_index += 1
                self._current_list_index = self._list_index
            self._list_depth += 1
        if tag == "p" and self._content_depth and not self._list_items:
            self._paragraph_item = {
                "parts": [],
                "download_url": None,
                "source_list": 0,
            }
        if tag == "a" and self._content_depth:
            self._anchor_href = attributes.get("href")
            self._anchor_parts = []
        elif tag == "li" and self._content_depth:
            self._list_items.append(
                {
                    "parts": [],
                    "download_url": None,
                    "source_list": self._current_list_index,
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._content_depth and self._anchor_href:
            self._finish_anchor()
            self._anchor_href = None
            self._anchor_parts = []
        elif tag == "li" and self._content_depth and self._list_items:
            self._finish_list_item(self._list_items.pop())
        elif tag == "p" and self._content_depth and self._paragraph_item:
            paragraph_text = " ".join(
                "".join(self._paragraph_item["parts"]).split()
            )
            self._record_parent_hint(paragraph_text)
            if not self._description_depth and not self._associated_depth:
                self._finish_metadata_block(paragraph_text)
            self._finish_list_item(self._paragraph_item)
            self._paragraph_item = None
        elif tag == self._heading_tag:
            self._finish_metadata_heading(
                " ".join("".join(self._heading_parts).split())
            )
            self._heading_tag = None
            self._heading_parts = []
            self._in_associated_heading = False
        elif tag in {"ol", "ul"} and self._content_depth and self._list_depth:
            self._list_depth -= 1
            if self._list_depth == 0:
                self._current_list_index = 0
        elif tag == "div":
            if self._associated_depth:
                self._associated_depth -= 1
            if self._description_depth:
                self._description_depth -= 1
            if self._content_depth:
                self._content_depth -= 1
        if tag == "p" and self._description_depth and not self._associated_depth:
            self._description_parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split()).casefold()
        if self._content_depth and not self._list_items:
            if re.search(
                r"\bofficial (?:translations?|translated versions?|versions?)\b",
                normalized,
            ):
                self._release_group = "Official translation"
            elif re.search(
                r"\bfan (?:translations?|translated versions?|versions?)\b",
                normalized,
            ):
                self._release_group = "Fan translation"
            elif re.fullmatch(r"pdf\s+light", normalized):
                self._release_group = "PDF (light)"
            elif re.fullmatch(r"pdf\s+dark", normalized):
                self._release_group = "PDF (dark)"
            elif re.fullmatch(r"pdf", normalized):
                self._release_group = "PDF"
            elif re.fullmatch(r"epub", normalized):
                self._release_group = "EPUB"
            elif re.fullmatch(
                r"download link\s*[-–—]\s*epub(?: file)?",
                normalized,
            ):
                self._release_group = "EPUB"
            elif re.fullmatch(
                r"download link\s*[-–—]\s*pdf(?: file)?",
                normalized,
            ):
                self._release_group = "PDF"
        if self._anchor_href:
            self._anchor_parts.append(data)
        if self._heading_tag:
            self._heading_parts.append(data)
        if self._list_items:
            self._list_items[-1]["parts"].append(data)
        elif self._paragraph_item:
            self._paragraph_item["parts"].append(data)
        if self._associated_depth:
            self._associated_parts.append(data)
        elif self._description_depth and not self._in_associated_heading:
            self._description_parts.append(data)

    def _finish_metadata_heading(self, text: str) -> None:
        normalized = text.strip(" :").casefold()
        if normalized in {"synopsis", "description", "summary"}:
            self._metadata_section = "synopsis"
        elif normalized in {"associated names", "alternative names", "alternate names"}:
            self._metadata_section = "associated"
        elif normalized in {
            "related series",
            "genres",
            "genre",
            "download",
            "downloads",
            "share this",
            "related",
        }:
            self._metadata_section = None

    def _record_parent_hint(self, text: str) -> None:
        if (
            re.search(r"\bvolume\s+[0-9]+(?:\.[0-9]+)?\b", text, re.IGNORECASE)
            and len(text.split()) >= 6
        ):
            self._parent_hints.append(text)

    def _finish_metadata_block(self, text: str) -> None:
        if not text:
            return
        marker = re.match(
            r"^(synopsis|description|summary|associated names|alternative names|"
            r"alternate names|related series|genres?)\s*[:\-–—]?\s*(.*)$",
            text,
            re.IGNORECASE,
        )
        if marker:
            heading = marker.group(1).casefold()
            remainder = marker.group(2).strip()
            if heading in {"synopsis", "description", "summary"}:
                self._metadata_section = "synopsis"
                if remainder:
                    self._description_parts.extend([remainder, "\n\n"])
            elif heading in {"associated names", "alternative names", "alternate names"}:
                self._metadata_section = "associated"
                if remainder:
                    self._associated_parts.extend([remainder, "\n"])
            else:
                self._metadata_section = None
            return

        if self._metadata_section == "synopsis" and not looks_like_download_text(text):
            self._description_parts.extend([text, "\n\n"])
        elif self._metadata_section == "associated":
            self._associated_parts.extend([text, "\n"])

    def _finish_anchor(self) -> None:
        href = urllib.parse.urljoin(self.post_url, self._anchor_href or "")
        text = " ".join("".join(self._anchor_parts).split())
        normalized = text.casefold()

        if re.fullmatch(r"refer to (?:the )?(?:original|main) post", normalized):
            if canonical_page_url(href) != canonical_page_url(self.post_url):
                self.original_post_url = href
            return

        parsed_href = urllib.parse.urlparse(href)
        genre = text.rstrip(", ")
        if (
            parsed_href.netloc.casefold() in {"jnovels.com", "www.jnovels.com"}
            and "/category/light-novels/" in parsed_href.path.casefold()
            and genre
            and genre not in self._genres
        ):
            self._genres.append(genre)
            return

        if (
            self._list_items
            and parsed_href.netloc.casefold() in {"jnovels.com", "www.jnovels.com"}
            and parsed_href.path not in {"/", "/faq/"}
            and "share=" not in parsed_href.query
        ):
            self._list_items[-1]["download_url"] = href
            return

        if not is_external_http_url(href):
            return
        if normalized in {"pdf", "epub"}:
            self._release_group = normalized.upper()
        catalog_label = extract_catalog_label(text)
        legacy_download = is_legacy_download_url(href)
        if self._list_items and (
            "download" in normalized
            or catalog_label
            or legacy_download
        ):
            self._list_items[-1]["download_url"] = href
        elif self._paragraph_item:
            self._paragraph_item["download_url"] = href
        if not self.download_url and (
            "download" in normalized
            or catalog_label
            or legacy_download
        ):
            self.download_url = href
        if catalog_label and not self._list_items:
            self._append_volume(catalog_label, href)

    def _finish_list_item(self, item: dict[str, Any]) -> None:
        download_url = item["download_url"]
        if not download_url:
            return
        text = " ".join("".join(item["parts"]).split())
        label = extract_catalog_label(text) or extract_trailing_catalog_label(text)
        if not label and self._release_group in {"PDF", "EPUB"}:
            label = "Complete edition"
        if not label and item["source_list"]:
            label = legacy_anchor_label(text)
        if not label and item["source_list"] == 0:
            label = "Complete edition"
        if not label:
            return
        self._append_volume(label, download_url, item["source_list"])

    def _append_volume(
        self,
        label: str,
        download_url: str,
        source_list: int = 0,
    ) -> None:
        if download_url in self._volume_urls:
            return
        key = (
            source_list,
            (self._release_group or "").casefold(),
            label.casefold(),
        )
        existing_index = self._volume_keys.get(key)
        if existing_index is not None:
            existing = self.volumes[existing_index]
            self._volume_urls.discard(existing.download_url)
            self._volume_urls.add(download_url)
            self.volumes[existing_index] = SeriesVolume(
                label,
                download_url,
                existing.position,
                self._release_group,
                source_list,
            )
            return
        self._volume_urls.add(download_url)
        self._volume_keys[key] = len(self.volumes)
        self.volumes.append(
            SeriesVolume(
                label,
                download_url,
                len(self.volumes),
                self._release_group,
                source_list,
            )
        )

    def details(self) -> PostDetails:
        entry_type = "volume" if self.original_post_url else "series"
        associated_names = unique_nonempty_lines("".join(self._associated_parts))
        synopsis = normalize_synopsis("".join(self._description_parts))
        volumes = self.volumes
        source_lists = sorted({volume.source_list for volume in volumes if volume.source_list})
        if len(source_lists) > 1:
            primary_list = source_lists[0]
            volumes = [
                volume
                for volume in volumes
                if volume.source_list in {0, primary_list}
                or volume.release_group is not None
            ]
        download_url = self.download_url
        epub_download = next(
            (
                volume.download_url
                for volume in volumes
                if volume.release_group == "EPUB"
            ),
            None,
        )
        if epub_download:
            download_url = epub_download
        return PostDetails(
            entry_type,
            self.original_post_url,
            download_url,
            volumes if entry_type == "series" else [],
            associated_names,
            self._genres,
            synopsis,
            self._parent_hints,
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = self._classes(attributes)

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
            self._url = attributes.get("href")
        elif tag == "a" and "post-date" in classes:
            self._in_date = True
        elif tag == "img" and self._in_featured and not self._image_url:
            self._image_url = attributes.get("src") or attributes.get("data-src")

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


def is_legacy_download_url(value: str) -> bool:
    host = urllib.parse.urlparse(value).netloc.casefold()
    return (
        host in {"charexempire.com", "www.charexempire.com", "enagato.com", "www.enagato.com"}
        or (
            host in {"mp4directs.com", "www.mp4directs.com"}
            and "/forums/" in urllib.parse.urlparse(value).path.casefold()
        )
    )


def legacy_anchor_label(value: str) -> str | None:
    cleaned = re.sub(
        r"\b(?:PDF|EPUB|DOWNLOAD|HERE|OPEN|READ)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = " ".join(cleaned.split()).strip(" -–—.:|")
    if not cleaned or len(cleaned) > 140:
        return None
    return normalize_legacy_title_case(cleaned)


def looks_like_download_text(value: str) -> bool:
    normalized = " ".join(value.split()).casefold()
    return bool(
        re.search(r"\b(?:download|volume|vol\.)\s*(?:link|here|[0-9])", normalized)
        or normalized.startswith(("available in ", "share this", "like this"))
    )


def canonical_page_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", "")
    )


def is_volume_label(value: str) -> bool:
    return extract_volume_label(value) is not None


def extract_volume_label(value: str) -> str | None:
    match = re.search(
        r"\b(?:"
        r"(?:(YEAR|PART)\s*([0-9]+))"
        r"|(?:([0-9]+)(?:ST|ND|RD|TH)\s+YEAR)"
        r"|(?:YR\s*([0-9]+))"
        r")?\s*"
        r"(?:VOLUME|VOLUMES|VOL\.?)\s*([0-9]+(?:\.[0-9]+)?)(\+*)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    number = normalize_volume_number(match.group(5))
    suffix = match.group(6)
    trailing = value[match.end() :]
    canto_match = re.match(r"\s+(CANTO\s+)?([IVX]+)\b", trailing, re.IGNORECASE)
    book_match = re.match(r"\s*\(\s*BOOK\s+([0-9]+)\s*\)", trailing, re.IGNORECASE)
    part_match = re.match(
        r"\s*(?:[-–—,]\s*)?(PART|ACT)\s*([0-9]+)\b",
        trailing,
        re.IGNORECASE,
    )
    named_half_match = re.match(
        r"\s+(EXORDIUM|FINALE)\b",
        trailing,
        re.IGNORECASE,
    )
    qualifier = ""
    if part_match:
        qualifier = (
            f" {part_match.group(1).title()} "
            f"{normalize_volume_number(part_match.group(2))}"
        )
    elif canto_match:
        canto_prefix = "Canto " if canto_match.group(1) else ""
        qualifier = f" {canto_prefix}{canto_match.group(2).upper()}"
    elif book_match:
        qualifier = f" (Book {normalize_volume_number(book_match.group(1))})"
    elif named_half_match:
        qualifier = f" {named_half_match.group(1).title()}"

    named_prefix = ""
    before_volume = value[: match.start()]
    if re.search(r"\bSHORT STORY COLLECTION\s*$", before_volume, re.IGNORECASE):
        named_prefix = "Short Story Collection "

    if match.group(1):
        prefix = match.group(1).title()
        prefix_number = normalize_volume_number(match.group(2))
        return f"{prefix} {prefix_number} {named_prefix}Volume {number}{suffix}{qualifier}"
    year_number = match.group(3) or match.group(4)
    if year_number:
        return (
            f"Year {normalize_volume_number(year_number)} "
            f"{named_prefix}Volume {number}{suffix}{qualifier}"
        )
    return f"{named_prefix}Volume {number}{suffix}{qualifier}"


def extract_named_release_label(value: str) -> str | None:
    cleaned = re.sub(
        r"\b(?:DOWNLOAD|OPEN|READ)\b\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = " ".join(cleaned.split()).strip(" -–—.:|")
    fanbook = re.search(
        r"\bFANBOOK(?:\s+(?:VOLUME|VOL\.?)?\s*([0-9]+(?:\.[0-9]+)?))?\b",
        cleaned,
        re.IGNORECASE,
    )
    if fanbook:
        number = fanbook.group(1)
        return f"Fanbook {normalize_volume_number(number)}" if number else "Fanbook"
    royal_academy = re.search(
        r"\bROYAL ACADEMY STORIES\b.*$",
        cleaned,
        re.IGNORECASE,
    )
    if royal_academy:
        return royal_academy.group(0).strip().title()
    match = re.search(
        r"\b(SIDE STORIES|DISC EX|GAIDEN|AFTER STORY)\b",
        cleaned,
        re.IGNORECASE,
    )
    return match.group(1).title() if match else None


def extract_catalog_label(value: str) -> str | None:
    return extract_volume_label(value) or extract_named_release_label(value)


def extract_trailing_catalog_label(value: str) -> str | None:
    without_action = re.sub(r"\bDOWNLOAD\b", "", value, flags=re.IGNORECASE)
    cleaned = without_action.rstrip(" -–—.:|")
    match = re.search(r"\b([0-9]+(?:\.[0-9]+)?)(\+*)$", cleaned)
    if not match:
        return None
    return f"Volume {normalize_volume_number(match.group(1))}{match.group(2)}"


def unique_nonempty_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split()).strip(" ,")
        if line and line not in lines:
            lines.append(line)
    return lines


def normalize_synopsis(value: str) -> str | None:
    paragraphs = [
        " ".join(paragraph.split())
        for paragraph in re.split(r"\n\s*\n", value)
        if paragraph.strip()
    ]
    return "\n\n".join(paragraphs) or None


def encode_string_list(values: list[str]) -> str | None:
    return json.dumps(values, ensure_ascii=False) if values else None


def decode_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in decoded if isinstance(item, str)] if isinstance(decoded, list) else []


def normalize_volume_number(value: str) -> str:
    whole, separator, fraction = value.partition(".")
    normalized_whole = str(int(whole))
    return f"{normalized_whole}.{fraction}" if separator else normalized_whole


def roman_to_integer(value: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10}
    total = 0
    previous = 0
    for character in reversed(value.upper()):
        current = values.get(character)
        if current is None:
            return None
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total or None


def compact_volume_badge(number: str, suffix: str) -> str:
    normalized_number = normalize_volume_number(number)
    qualifier = re.search(
        r"\b(Part|Act|Canto)\s+([0-9]+|[IVX]+)\b",
        suffix,
        re.IGNORECASE,
    )
    if qualifier:
        part = qualifier.group(2)
        part_number = (
            normalize_volume_number(part)
            if part.isdecimal()
            else str(roman_to_integer(part) or part.upper())
        )
        return f"Vol. {normalized_number}.{part_number}"
    roman_half = re.match(r"\s+([IVX]+)\b", suffix, re.IGNORECASE)
    if roman_half:
        part_number = roman_to_integer(roman_half.group(1))
        if part_number:
            return f"Vol. {normalized_number}.{part_number}"
    named = re.search(r"\b(Exordium|Finale)\b", suffix, re.IGNORECASE)
    if named:
        return f"Vol. {normalized_number} {named.group(1).title()}"
    return f"Vol. {normalized_number}"


def normalize_legacy_title_case(value: str) -> str:
    letters = [character for character in value if character.isalpha()]
    if not letters or any(character.islower() for character in letters):
        return value

    minor_words = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "from",
        "in",
        "into",
        "nor",
        "of",
        "on",
        "or",
        "over",
        "per",
        "the",
        "to",
        "up",
        "via",
        "with",
        "yet",
    }
    word_matches = list(re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?", value))
    if not word_matches:
        return value

    first_start = word_matches[0].start()
    last_start = word_matches[-1].start()

    def title_word(match: re.Match[str]) -> str:
        word = match.group(0).casefold()
        if (
            word in minor_words
            and match.start() not in {first_start, last_start}
        ):
            return word
        return word[:1].upper() + word[1:]

    return re.sub(r"[A-Za-z]+(?:['’][A-Za-z]+)?", title_word, value)


def normalize_display_title(value: str) -> str:
    has_pdf = bool(
        re.search(r"\[PDF\]", value, re.IGNORECASE)
        or re.search(r"\sPDF\s*$", value, re.IGNORECASE)
    )
    has_epub = bool(
        re.search(r"\[EPUB\]", value, re.IGNORECASE)
        or re.search(r"\sEPUB\s*$", value, re.IGNORECASE)
    )
    cleaned = re.sub(
        r"^(?:\s*\[(?:WEB\s*NOVEL|WEBNOVEL|PDF|EPUB)\]\s*)+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\s*DOWNLOAD\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(?:\s+(?:PDF|EPUB))+\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+ALL\s+(?:(?:LN|LIGHT\s+NOVEL)\s+)?VOLUMES?\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = normalize_legacy_title_case(" ".join(cleaned.split()))
    if has_pdf and not has_epub:
        cleaned = f"{cleaned} PDF"
    return cleaned


def parse_book_identity(title: str, url: str) -> tuple[str, str | None]:
    cleaned = normalize_display_title(title)
    cleaned = re.sub(r"\s+Epub$", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(
        r"\s+Epub(?=\s+(?:Canto|Part|Act)\s+(?:[0-9]+|[IVX]+)\s*$)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    match = re.search(
        r"\s+Volume\s+([0-9]+(?:\.[0-9]+)?)"
        r"((?:\s+(?:Canto\s+[IVX]+|Part\s+[0-9]+|Act\s+[0-9]+|[IVX]+|Exordium|Finale))?"
        r"(?:\s*[:\-–—]\s+.+?)?)"
        r"(?:\s+Light\s+Novel)?(?:\s+(PDF))?$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if match:
        series_title = cleaned[: match.start()].rstrip(" .…")
        if match.group(3):
            series_title = f"{series_title} PDF"
        return series_title, compact_volume_badge(match.group(1), match.group(2))

    slug_match = re.search(
        r"-volume-([0-9]+(?:\.[0-9]+)?)(?:-light-novel)?-(?:epub|pdf)/?$",
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


def series_identity_key(title: str | None, url: str) -> str:
    if title:
        series_title, _ = parse_book_identity(title, url)
        value = series_title
    else:
        value = urllib.parse.unquote(urllib.parse.urlparse(url).path.strip("/"))
        value = value.replace("-", " ")
        value = re.sub(
            r"\bvolume\s+[0-9]+(?:\.[0-9]+)?\b.*$",
            "",
            value,
            flags=re.IGNORECASE,
        )
    value = re.sub(r"^\s*download\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\b(?:all\s+volumes?|light\s+novels?|epubs?|pdfs?)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(?:volume|vol\.?)\s*[0-9]+(?:\.[0-9]+)?\b.*$",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(?:part|year)\s+[0-9]+\s*$",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def book_page_format(title: str, url: str) -> str | None:
    value = f"{title} {urllib.parse.urlparse(url).path}".casefold()
    has_pdf = bool(
        "[pdf]" in value
        or re.search(r"(?:\s|-)pdf(?:/)?$", value)
        or "pdfepub" in value
    )
    has_epub = bool(
        "[epub]" in value
        or re.search(r"(?:\s|-)epub(?:/)?$", value)
        or "pdfepub" in value
    )
    if has_pdf and has_epub:
        return "mixed"
    if has_pdf:
        return "pdf"
    if has_epub:
        return "epub"
    return None


def book_duplicate_key(title: str, url: str) -> tuple[str, str]:
    _, volume_label = parse_book_identity(title, url)
    return series_identity_key(title, url), (volume_label or "").casefold()


def delete_book_urls(
    connection: sqlite3.Connection,
    urls: set[str],
) -> int:
    if not urls:
        return 0
    placeholders = ", ".join("?" for _ in urls)
    params = tuple(sorted(urls))
    connection.execute(
        f"DELETE FROM series_volumes WHERE series_url IN ({placeholders})",
        params,
    )
    connection.execute(
        f"DELETE FROM series_status WHERE series_url IN ({placeholders})",
        params,
    )
    before = connection.total_changes
    connection.execute(
        f"DELETE FROM books WHERE url IN ({placeholders})",
        params,
    )
    return connection.total_changes - before


def prune_pdf_duplicates(
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    def prune(active_connection: sqlite3.Connection) -> int:
        rows = active_connection.execute(
            "SELECT title, url, original_post_url FROM books"
        ).fetchall()
        preferred_keys = {
            book_duplicate_key(row["title"], row["url"])
            for row in rows
            if book_page_format(row["title"], row["url"]) in {"epub", "mixed"}
        }
        duplicate_urls = {
            row["url"]
            for row in rows
            if book_page_format(row["title"], row["url"]) == "pdf"
            and book_duplicate_key(row["title"], row["url"]) in preferred_keys
        }
        duplicate_series_urls = {
            row["url"]
            for row in rows
            if row["url"] in duplicate_urls and not row["original_post_url"]
        }
        duplicate_urls.update(
            row["url"]
            for row in rows
            if row["original_post_url"] in duplicate_series_urls
            and book_page_format(row["title"], row["url"]) == "pdf"
        )
        deleted = delete_book_urls(active_connection, duplicate_urls)
        if deleted:
            invalidate_catalog_indexes()
        return deleted

    if connection is not None:
        return prune(connection)
    with connect_db() as active_connection:
        return prune(active_connection)


def invalidate_catalog_indexes() -> None:
    global _catalog_index_cache
    with _catalog_index_lock:
        _catalog_index_cache = None


def get_catalog_indexes() -> tuple[
    dict[str, tuple[str, int]],
    dict[str, tuple[str, int]],
    dict[str, int],
    dict[str, int],
]:
    global _catalog_index_cache
    with _catalog_index_lock:
        if _catalog_index_cache is not None and _catalog_index_cache[0] == DB_PATH:
            return _catalog_index_cache[1:]

        with connect_db() as connection:
            rows = connection.execute(
                """
        SELECT sv.series_url,
               sv.download_url,
               sv.label,
               sv.release_group,
               counts.volume_count,
               b.title
                FROM series_volumes AS sv
                JOIN (
                    SELECT series_url, COUNT(*) AS volume_count
                    FROM series_volumes
                    GROUP BY series_url
                ) AS counts ON counts.series_url = sv.series_url
                LEFT JOIN books AS b ON b.url = sv.series_url
                ORDER BY counts.volume_count DESC, sv.series_url
                """
            ).fetchall()

        catalog_index: dict[str, tuple[str, int]] = {}
        download_index: dict[str, tuple[str, int]] = {}
        volume_counts: dict[str, int] = {}
        category_labels: dict[tuple[str, str], set[str]] = {}
        for row in rows:
            candidate = (row["series_url"], row["volume_count"])
            volume_counts[row["series_url"]] = row["volume_count"]
            key = series_identity_key(row["title"], row["series_url"])
            if key and candidate[1] > catalog_index.get(key, ("", 0))[1]:
                catalog_index[key] = candidate
            download_index.setdefault(row["download_url"], candidate)
            category_key = (
                row["series_url"],
                (row["release_group"] or "").casefold(),
            )
            category_labels.setdefault(category_key, set()).add(
                row["label"].casefold()
            )

        download_category_counts = {
            row["download_url"]: len(
                category_labels[
                    (
                        row["series_url"],
                        (row["release_group"] or "").casefold(),
                    )
                ]
            )
            for row in rows
        }

        _catalog_index_cache = (
            DB_PATH,
            catalog_index,
            download_index,
            volume_counts,
            download_category_counts,
        )
        return catalog_index, download_index, volume_counts, download_category_counts


def get_series_catalog_index() -> dict[str, tuple[str, int]]:
    catalog_index, _, _, _ = get_catalog_indexes()
    return catalog_index


def get_download_catalog_index() -> dict[str, tuple[str, int]]:
    _, download_index, _, _ = get_catalog_indexes()
    return download_index


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_db() -> None:
    invalidate_catalog_indexes()
    with connect_db() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
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
                detail_last_error TEXT,
                associated_names TEXT,
                genres TEXT,
                synopsis TEXT
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
                checked_at TEXT NOT NULL,
                metadata_checked_at TEXT,
                unavailable_at TEXT
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
            "associated_names": "ALTER TABLE books ADD COLUMN associated_names TEXT",
            "genres": "ALTER TABLE books ADD COLUMN genres TEXT",
            "synopsis": "ALTER TABLE books ADD COLUMN synopsis TEXT",
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
        status_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(series_status)").fetchall()
        }
        if "metadata_checked_at" not in status_columns:
            connection.execute(
                "ALTER TABLE series_status ADD COLUMN metadata_checked_at TEXT"
            )
        if "unavailable_at" not in status_columns:
            connection.execute(
                "ALTER TABLE series_status ADD COLUMN unavailable_at TEXT"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_books_original_post_cached
            ON books(original_post_url, cached_at)
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO series_status (series_url, checked_at)
            SELECT series_url, MAX(cached_at)
            FROM series_volumes
            GROUP BY series_url
            """
        )
        connection.executemany(
            "DELETE FROM books WHERE url = ?",
            [(urllib.parse.urljoin(BASE_URL, path),) for path in EXCLUDED_SEARCH_PATHS],
        )
    build_missing_fallback_catalogs()
    prepare_legacy_reparse()
    prune_pdf_duplicates()
    cleanup_misclassified_series_tracking()


def upsert_books(books: list[Book]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        existing_rows = connection.execute(
            "SELECT title, url, original_post_url FROM books"
        ).fetchall()
        preferred_keys = {
            book_duplicate_key(row["title"], row["url"])
            for row in existing_rows
            if book_page_format(row["title"], row["url"]) in {"epub", "mixed"}
        }
        preferred_keys.update(
            book_duplicate_key(book.title, book.url)
            for book in books
            if book_page_format(book.title, book.url) in {"epub", "mixed"}
        )
        books = [
            book
            for book in books
            if book_page_format(book.title, book.url) != "pdf"
            or book_duplicate_key(book.title, book.url) not in preferred_keys
        ]
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
        prune_pdf_duplicates(connection=connection)
        changes = connection.total_changes - before
    if changes:
        invalidate_catalog_indexes()
    return changes


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


def get_metadata(
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, str]:
    def load(active_connection: sqlite3.Connection) -> dict[str, str]:
        rows = active_connection.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: row["value"] for row in rows}

    if connection is not None:
        return load(connection)
    with connect_db() as active_connection:
        return load(active_connection)


def get_public_metadata(
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, str]:
    metadata = get_metadata(connection=connection)
    metadata.pop(SYNC_CHECKPOINT_KEY, None)
    metadata.pop(LEGACY_REPARSE_KEY, None)
    return metadata


def prepare_legacy_reparse() -> int:
    with connect_db() as connection:
        if connection.execute(
            "SELECT 1 FROM metadata WHERE key = ?",
            (LEGACY_REPARSE_KEY,),
        ).fetchone():
            return 0

        before = connection.total_changes
        connection.execute(
            """
            UPDATE books
            SET entry_type = 'series',
                original_post_url = NULL
            WHERE original_post_url = url
            """
        )
        connection.execute(
            """
            UPDATE series_status
            SET metadata_checked_at = NULL
            WHERE series_url IN (
                SELECT url
                FROM books
                WHERE entry_type = 'series'
                  AND (
                      lower(title) LIKE '%webnovel%'
                      OR lower(title) LIKE '%[pdf]%'
                      OR lower(title) LIKE '%[epub]%'
                      OR lower(url) LIKE '%pdfepub%'
                  )
            )
            """
        )
        changes = connection.total_changes - before
        connection.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            (LEGACY_REPARSE_KEY, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    return changes


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
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == HTTPStatus.NOT_FOUND and page > 1:
            return []
        raise

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


def infer_series_parent(
    url: str,
    parent_hints: list[str] | None = None,
    download_url: str | None = None,
) -> str | None:
    with connect_db() as connection:
        if download_url:
            exact_parent = connection.execute(
                """
                SELECT sv.series_url
                FROM series_volumes AS sv
                WHERE sv.download_url = ?
                  AND sv.series_url <> ?
                ORDER BY (
                    SELECT COUNT(*)
                    FROM series_volumes AS sibling
                    WHERE sibling.series_url = sv.series_url
                ) DESC,
                sv.series_url
                LIMIT 1
                """,
                (download_url, url),
            ).fetchone()
            if exact_parent:
                return exact_parent["series_url"]
        book = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (url,),
        ).fetchone()
        if not book:
            return None
        _, volume_label = parse_book_identity(book["title"], url)
        if not volume_label:
            return None
        catalog_volume_label = re.sub(
            r"^Vol\.\s+",
            "Volume ",
            volume_label,
            flags=re.IGNORECASE,
        )
        child_keys = [series_identity_key(book["title"], url)]
        child_keys.extend(
            series_identity_key(hint, url)
            for hint in parent_hints or []
        )
        candidates = connection.execute(
            """
            SELECT b.title,
                   b.url,
                   (
                       SELECT COUNT(*)
                       FROM series_volumes AS count_volume
                       WHERE count_volume.series_url = b.url
                   ) AS volume_count,
                   EXISTS (
                       SELECT 1
                       FROM series_volumes AS matching_volume
                       WHERE matching_volume.series_url = b.url
                         AND lower(matching_volume.label) = lower(?)
                   ) AS has_matching_volume
            FROM books AS b
            WHERE b.entry_type = 'series'
              AND b.url <> ?
              AND EXISTS (
                  SELECT 1
                  FROM series_volumes AS sv
                  WHERE sv.series_url = b.url
              )
            """,
            (catalog_volume_label, url),
        ).fetchall()

    matches: list[tuple[int, str]] = []
    for candidate in candidates:
        candidate_key = series_identity_key(candidate["title"], candidate["url"])
        candidate_words = candidate_key.split()
        title_prefix_matches = any(
            child_key.startswith(f"{candidate_key} ")
            for child_key in child_keys
        )
        if title_prefix_matches and (
            len(candidate_words) >= 5
            or (
                len(candidate_words) >= 2
                and candidate["volume_count"] >= 3
                and candidate["has_matching_volume"]
            )
        ):
            matches.append((len(candidate_words), candidate["url"]))
    return max(matches, default=(0, ""))[1] or None


def update_book_details(url: str, details: PostDetails) -> None:
    if details.entry_type == "series" and not details.original_post_url:
        inferred_parent = infer_series_parent(
            url,
            details.parent_hints,
            details.download_url,
        )
        if inferred_parent:
            details.entry_type = "volume"
            details.original_post_url = inferred_parent
            details.volumes = []

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    associated_names = encode_string_list(details.associated_names)
    genres = encode_string_list(details.genres)
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
                detail_last_error = NULL,
                associated_names = COALESCE(?, associated_names),
                genres = COALESCE(?, genres),
                synopsis = COALESCE(?, synopsis)
            WHERE url = ?
            """,
            (
                details.entry_type,
                details.original_post_url,
                details.download_url,
                now,
                now,
                associated_names,
                genres,
                details.synopsis,
                url,
            ),
        )
        if details.entry_type == "series":
            connection.execute(
                """
                UPDATE books
                SET associated_names = COALESCE(?, associated_names),
                    genres = COALESCE(?, genres),
                    synopsis = COALESCE(?, synopsis)
                WHERE original_post_url = ?
                """,
                (associated_names, genres, details.synopsis, url),
            )
    if details.entry_type == "series":
        replace_series_volumes(url, details.volumes)
        build_fallback_catalog(url)
        mark_series_metadata_checked(url, now)
        reconcile_series_downloads(url)
    elif details.original_post_url:
        build_fallback_catalog(details.original_post_url)


def validate_post_details(details: PostDetails) -> None:
    if not details.download_url and not details.volumes:
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
            ON CONFLICT(series_url) DO UPDATE
            SET checked_at = excluded.checked_at,
                unavailable_at = NULL
            """,
            (series_url, now),
        )
    invalidate_catalog_indexes()


def mark_series_metadata_checked(series_url: str, checked_at: str | None = None) -> None:
    checked_at = checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO series_status (series_url, checked_at, metadata_checked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(series_url) DO UPDATE
            SET metadata_checked_at = excluded.metadata_checked_at,
                unavailable_at = NULL
            """,
            (series_url, checked_at, checked_at),
        )


def mark_series_unavailable(series_url: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO series_status
                (series_url, checked_at, metadata_checked_at, unavailable_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(series_url) DO UPDATE
            SET checked_at = excluded.checked_at,
                metadata_checked_at = excluded.metadata_checked_at,
                unavailable_at = excluded.unavailable_at
            """,
            (series_url, now, now, now),
        )


def build_fallback_catalog(series_url: str) -> int:
    with connect_db() as connection:
        existing_rows = connection.execute(
            """
            SELECT label, download_url, position
            FROM series_volumes
            WHERE series_url = ?
            ORDER BY position
            """,
            (series_url,),
        ).fetchall()
        rows = connection.execute(
            """
            SELECT title, download_url
            FROM books
            WHERE original_post_url = ?
              AND download_url IS NOT NULL
            ORDER BY published_at, id
            """,
            (series_url,),
        ).fetchall()

    existing_labels = {row["label"].casefold() for row in existing_rows}
    existing_downloads = {row["download_url"] for row in existing_rows}
    next_position = max((row["position"] for row in existing_rows), default=-1) + 1
    volumes: list[SeriesVolume] = []
    for row in rows:
        label = extract_catalog_label(row["title"])
        download_url = row["download_url"]
        if (
            not label
            or label.casefold() in existing_labels
            or download_url in existing_downloads
        ):
            continue
        existing_labels.add(label.casefold())
        existing_downloads.add(download_url)
        volumes.append(
            SeriesVolume(label, download_url, next_position + len(volumes))
        )
    if not volumes:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO series_volumes
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
                    None,
                )
                for volume in volumes
            ],
        )
        connection.execute(
            """
            INSERT INTO series_status (series_url, checked_at)
            VALUES (?, ?)
            ON CONFLICT(series_url) DO NOTHING
            """,
            (series_url, now),
        )
    invalidate_catalog_indexes()
    return len(volumes)


def build_missing_fallback_catalogs() -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT original_post_url
            FROM books
            WHERE entry_type = 'volume'
              AND original_post_url IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM series_volumes AS sv
                  WHERE sv.series_url = books.original_post_url
              )
            """
        ).fetchall()
    return sum(build_fallback_catalog(row["original_post_url"]) for row in rows)


def reparent_series_alias(old_series_url: str, new_series_url: str) -> int:
    with connect_db() as connection:
        before = connection.total_changes
        connection.execute(
            """
            UPDATE books
            SET original_post_url = ?
            WHERE original_post_url = ?
            """,
            (new_series_url, old_series_url),
        )
        connection.execute(
            "DELETE FROM series_volumes WHERE series_url = ?",
            (old_series_url,),
        )
        connection.execute(
            "DELETE FROM series_status WHERE series_url = ?",
            (old_series_url,),
        )
        changes = connection.total_changes - before
    build_fallback_catalog(new_series_url)
    invalidate_catalog_indexes()
    return changes


def cleanup_misclassified_series_tracking() -> int:
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT ss.series_url, b.original_post_url
            FROM series_status AS ss
            JOIN books AS b ON b.url = ss.series_url
            WHERE b.entry_type = 'volume'
              AND b.original_post_url IS NOT NULL
              AND b.original_post_url <> b.url
            """
        ).fetchall()
    return sum(
        reparent_series_alias(row["series_url"], row["original_post_url"])
        for row in rows
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


def get_cache_health(
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, int]:
    def load(active_connection: sqlite3.Connection) -> dict[str, int]:
        pending = active_connection.execute(
            """
            SELECT COUNT(*)
            FROM books AS b
            WHERE b.detail_cached_at IS NULL
               OR b.entry_type IS NULL
               OR b.download_url IS NULL
            """
        ).fetchone()[0]
        paused = active_connection.execute(
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
        failed = active_connection.execute(
            """
            SELECT COUNT(*)
            FROM books
            WHERE detail_attempts > 0
              AND detail_last_error IS NOT NULL
            """
        ).fetchone()[0]
        stale_series = count_stale_series(connection=active_connection)
        refreshable_series = active_connection.execute(
            f"""
            SELECT COUNT(*)
            FROM series_status AS ss
            WHERE ss.unavailable_at IS NULL
              AND (
                    ss.metadata_checked_at IS NULL
                    OR {stale_series_where_clause()}
                  )
            """,
            (series_stale_cutoff(),),
        ).fetchone()[0]
        return {
            "pending_details": pending,
            "failed_retries": failed,
            "paused_retries": paused,
            "stale_series": stale_series,
            "refreshable_series": refreshable_series,
        }

    if connection is not None:
        return load(connection)
    with connect_db() as active_connection:
        return load(active_connection)


def series_stale_cutoff() -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=SERIES_STALE_DAYS)
    ).isoformat(timespec="seconds")


def stale_series_where_clause() -> str:
    return """
        (
            ss.unavailable_at IS NULL
            AND (
                ss.checked_at < ?
                OR EXISTS (
                    SELECT 1
                    FROM books AS b
                    WHERE b.original_post_url = ss.series_url
                      AND b.cached_at > ss.checked_at
                )
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


def get_series_metadata_backfill_urls(
    limit: int,
    *,
    exclude_urls: set[str] | None = None,
) -> list[str]:
    conditions = ["metadata_checked_at IS NULL", "unavailable_at IS NULL"]
    params: list[Any] = []
    if exclude_urls:
        placeholders = ", ".join("?" for _ in exclude_urls)
        conditions.append(f"series_url NOT IN ({placeholders})")
        params.extend(sorted(exclude_urls))
    params.append(limit)

    with connect_db() as connection:
        rows = connection.execute(
            f"""
            SELECT series_url
            FROM series_status
            WHERE {" AND ".join(conditions)}
            ORDER BY checked_at DESC, series_url
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [row["series_url"] for row in rows]


def get_series_refresh_urls(limit: int) -> list[str]:
    urls = get_stale_series_urls(limit)
    remaining = limit - len(urls)
    if remaining > 0:
        urls.extend(
            get_series_metadata_backfill_urls(remaining, exclude_urls=set(urls))
        )
    return urls


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
auto_sync_lock = threading.Lock()
last_auto_sync_started: float | None = None
sync_state: dict[str, Any] = {
    "running": False,
    "mode": None,
    "phase": None,
    "page": 0,
    "search_end_page": None,
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
    "queued_details": 0,
    "queued_series": 0,
    "request_delay": None,
    "phase_started_at": None,
    "error": None,
    "notice": None,
    "started_at": None,
    "finished_at": None,
}


def set_sync_started(
    start_page: int,
    max_pages: int,
    delay: float,
    mode: str,
) -> None:
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sync_state.update(
        {
            "running": True,
            "mode": mode,
            "phase": "search",
            "page": start_page,
            "search_end_page": None,
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
            "queued_details": 0,
            "queued_series": 0,
            "request_delay": delay,
            "phase_started_at": started_at,
            "error": None,
            "notice": None,
            "started_at": started_at,
            "finished_at": None,
        }
    )


def start_sync_thread(
    start_page: int,
    max_pages: int,
    delay: float,
    mode: str,
    detail_urls_override: list[str] | None = None,
    series_urls_override: list[str] | None = None,
    recovery: bool = False,
    *,
    name: str = "jnovels-sync",
) -> bool:
    if not sync_lock.acquire(blocking=False):
        return False

    set_sync_started(start_page, max_pages, delay, mode)
    thread = threading.Thread(
        target=run_sync,
        args=(
            start_page,
            max_pages,
            delay,
            mode,
            detail_urls_override,
            series_urls_override,
            recovery,
            True,
        ),
        name=name,
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        sync_state["running"] = False
        sync_lock.release()
        raise
    return True


def start_auto_catchup(
    max_pages: int = 20,
    delay: float = 1.0,
) -> tuple[bool, str | None, int]:
    global last_auto_sync_started
    with auto_sync_lock:
        now = time.monotonic()
        if last_auto_sync_started is not None:
            elapsed = now - last_auto_sync_started
            if elapsed < AUTO_SYNC_COOLDOWN_SECONDS:
                retry_after = max(int(AUTO_SYNC_COOLDOWN_SECONDS - elapsed + 0.999), 1)
                return False, "rate_limited", retry_after

        if not start_sync_thread(1, max_pages, delay, "catchup"):
            return False, "sync_running", 0

        last_auto_sync_started = now
        return True, None, 0


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
            if not fetched_pages[page]:
                sync_state["search_end_page"] = page
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
    lock_acquired: bool = False,
) -> None:
    if not lock_acquired and not sync_lock.acquire(blocking=False):
        return

    try:
        set_sync_started(start_page, max_pages, delay, mode)
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
            scheduled_series_urls.update(get_series_refresh_urls(max_pages))
        elif mode == "repair":
            books: list[Book] = []
            detail_urls = get_incomplete_detail_urls(max_pages)
        else:
            known_urls = get_cached_urls()

            def persist_page(page: int, page_books: list[Book]) -> None:
                upsert_books(page_books)
                cached_urls = get_cached_urls()
                page_books[:] = [
                    book for book in page_books if book.url in cached_urls
                ]
                pending = checkpoint["pending_detail_urls"]
                pending.extend(book.url for book in page_books if book.url not in pending)
                sync_state["queued_details"] = len(pending)
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
            cached_urls = get_cached_urls()
            detail_urls = list(
                dict.fromkeys(
                    book.url for book in books if book.url in cached_urls
                )
            )
            detail_urls.extend(
                get_incomplete_detail_urls(
                    AUTO_REPAIR_LIMIT,
                    max_attempts=AUTO_REPAIR_MAX_ATTEMPTS,
                    exclude_urls=set(detail_urls),
                )
            )
            scheduled_series_urls.update(get_series_refresh_urls(AUTO_SERIES_REFRESH_LIMIT))
        sync_state["phase"] = "details"
        sync_state["phase_started_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        sync_state["details_total"] = len(detail_urls)
        sync_state["queued_details"] = len(detail_urls)
        sync_state["queued_series"] = len(scheduled_series_urls)
        checkpoint["phase"] = "details"
        checkpoint["pending_detail_urls"] = list(dict.fromkeys(detail_urls))
        checkpoint["pending_series_urls"] = sorted(scheduled_series_urls)
        save_sync_checkpoint(checkpoint)
        discovered_series_urls: set[str] = set()
        detail_url_set = set(detail_urls)
        for index, url in enumerate(detail_urls):
            time.sleep(delay)
            completed = False
            try:
                details = fetch_post_details(url)
                update_book_details(url, details)
                if details.entry_type == "series":
                    discovered_series_urls.add(url)
                elif details.original_post_url:
                    discovered_series_urls.add(details.original_post_url)
                validate_post_details(details)
                completed = True
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                record_detail_failure(url, exc)
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
                    checkpoint["pending_series_urls"] = sorted(
                        scheduled_series_urls | discovered_series_urls
                    )
                    save_sync_checkpoint(checkpoint)

        missing_series_urls = sorted(
            (discovered_series_urls | scheduled_series_urls) - detail_url_set
        )
        sync_state["phase"] = "series"
        sync_state["phase_started_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        sync_state["series_total"] = len(missing_series_urls)
        sync_state["queued_series"] = len(missing_series_urls)
        checkpoint["phase"] = "series"
        checkpoint["pending_detail_urls"] = []
        checkpoint["pending_series_urls"] = missing_series_urls
        save_sync_checkpoint(checkpoint)
        for index, series_url in enumerate(missing_series_urls):
            time.sleep(delay)
            completed = False
            try:
                details = fetch_post_details(series_url)
                update_book_details(series_url, details)
                validate_post_details(details)
                completed = True
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                record_detail_failure(series_url, exc)
                sync_state["series_errors"] += 1
                completed = True
            finally:
                if completed:
                    sync_state["series_completed"] += 1
                    sync_state["queued_series"] = len(missing_series_urls) - index - 1
                    checkpoint["pending_series_urls"] = missing_series_urls[index + 1 :]
                    save_sync_checkpoint(checkpoint)

        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
            set_metadata("last_sync_recovered_at", finished)
        else:
            if sync_state["search_end_page"] is not None:
                sync_state["notice"] = (
                    "Reached the end of JNovels search results "
                    f"at page {sync_state['search_end_page']}."
                )
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
        sync_state["phase_started_at"] = None
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
        terms = re.findall(r"[\w%\\]+", query, flags=re.UNICODE)
        for term in terms:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if term.isdecimal():
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


def count_book_filters() -> dict[str, int]:
    with connect_db() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS all_count,
                   SUM(entry_type = 'series') AS series_count,
                   SUM(entry_type = 'volume') AS volume_count,
                   SUM(
                       detail_cached_at IS NULL
                       OR entry_type IS NULL
                       OR download_url IS NULL
                   ) AS pending_count
            FROM books
            """
        ).fetchone()
    return {
        "all": row["all_count"] or 0,
        "series": row["series_count"] or 0,
        "volumes": row["volume_count"] or 0,
        "pending": row["pending_count"] or 0,
    }


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
               entry_type, original_post_url, download_url, detail_cached_at,
               associated_names, genres, synopsis,
               (
                   SELECT sibling.title
                   FROM books AS sibling
                   WHERE sibling.original_post_url =
                         COALESCE(books.original_post_url, books.url)
                   ORDER BY sibling.published_at IS NULL,
                            sibling.published_at DESC,
                            sibling.id DESC
                   LIMIT 1
               ) AS latest_series_title
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
    (
        catalog_index,
        download_catalog_index,
        volume_counts,
        download_category_counts,
    ) = get_catalog_indexes()

    for book in books:
        book["associated_names"] = decode_string_list(book["associated_names"])
        book["genres"] = decode_string_list(book["genres"])
        series_title, volume_label = parse_book_identity(book["title"], book["url"])
        if book["latest_series_title"]:
            series_title, _ = parse_book_identity(
                book["latest_series_title"],
                book["original_post_url"] or book["url"],
            )
        series_url = (
            book["original_post_url"]
            if book["entry_type"] == "volume"
            else book["url"] if book["entry_type"] == "series" else None
        )
        catalog = catalog_index.get(series_identity_key(book["title"], book["url"]))
        download_catalog = download_catalog_index.get(book["download_url"] or "")
        if download_catalog and download_catalog[1] > volume_counts.get(series_url or "", 0):
            series_url = download_catalog[0]
        if catalog and catalog[1] > volume_counts.get(series_url or "", 0):
            series_url = catalog[0]
        book["series_title"] = series_title
        book["volume_label"] = volume_label
        book["series_url"] = series_url
        book["volume_count"] = volume_counts.get(series_url or "", 0)
        book["catalog_count"] = download_category_counts.get(
            book["download_url"] or "",
            book["volume_count"],
        )
        title_and_url = f"{book['title']} {book['url']}".casefold()
        book["catalog_kind"] = (
            "downloads"
            if "webnovel" in title_and_url
            or "[pdf]" in title_and_url
            or "[epub]" in title_and_url
            or "pdfepub" in title_and_url
            else "volumes"
        )
        book["is_standalone"] = (
            book["entry_type"] == "series" and book["volume_count"] == 1
        )
    return books


def list_series_volumes(series_url: str) -> list[dict[str, Any]]:
    with connect_db() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT label, download_url, position, cached_at, release_group
                FROM series_volumes
                WHERE series_url = ?
                ORDER BY position
                """,
                (series_url,),
            ).fetchall()
        ]
        series_book = connection.execute(
            "SELECT title FROM books WHERE url = ?",
            (series_url,),
        ).fetchone()
        book_rows = connection.execute(
            """
            SELECT title, url, download_url, published_at, entry_type,
                   original_post_url
            FROM books
            WHERE download_url IS NOT NULL
            ORDER BY entry_type = 'volume' DESC, published_at DESC
            """
        ).fetchall()

    target_key = series_identity_key(
        series_book["title"] if series_book else None,
        series_url,
    )
    matches: dict[str, list[sqlite3.Row]] = {}
    exact_downloads: dict[str, sqlite3.Row] = {}
    for book in book_rows:
        if book["download_url"]:
            exact_downloads.setdefault(book["download_url"], book)
        if (
            canonical_page_url(book["original_post_url"] or "") !=
            canonical_page_url(series_url)
            and series_identity_key(book["title"], book["url"]) != target_key
        ):
            continue
        label = extract_catalog_label(book["title"])
        if label:
            matches.setdefault(label.casefold(), []).append(book)

    for volume in rows:
        book = exact_downloads.get(volume["download_url"])
        if book is None:
            candidates = matches.get(volume["label"].casefold(), [])
            book = max(
                candidates,
                key=lambda candidate: candidate["published_at"] or "",
                default=None,
            )
        volume["published_at"] = book["published_at"] if book else None
        volume["post_url"] = book["url"] if book else None

    format_groups = {"pdf", "pdf (light)", "pdf (dark)", "epub"}
    dated_formats: dict[str, dict[str, Any]] = {}
    for volume in rows:
        if (
            (volume["release_group"] or "").casefold() in format_groups
            and volume["published_at"]
        ):
            dated_formats.setdefault(volume["label"].casefold(), volume)
    for volume in rows:
        if (
            (volume["release_group"] or "").casefold() not in format_groups
            or volume["published_at"]
        ):
            continue
        dated = dated_formats.get(volume["label"].casefold())
        if dated:
            volume["published_at"] = dated["published_at"]
            volume["post_url"] = dated["post_url"]
    return rows


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
            filter_counts = count_book_filters()
            if entry_filter != "all" and not filter_counts[entry_filter]:
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
                    "filter_counts": filter_counts,
                }
            )
            return
        if parsed.path == "/api/status":
            with connect_db() as connection:
                count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
                metadata = get_public_metadata(connection=connection)
                health = get_cache_health(connection=connection)
            self.send_json(
                {
                    "sync": dict(sync_state),
                    "count": count,
                    "metadata": metadata,
                    "health": health,
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
                started = start_sync_thread(
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
            }:
                raise ValueError("Unknown sync mode")
            start_page = min(max(int(payload.get("start_page", 1)), 1), 10000)
            max_limit = 2000 if mode in {"repair", "series_refresh"} else 200
            max_pages = min(max(int(payload.get("max_pages", 20)), 1), max_limit)
            delay = min(max(float(payload.get("delay", 1.0)), 0.5), 10.0)
            automatic = payload.get("automatic") is True
        except (ValueError, TypeError, json.JSONDecodeError):
            self.send_json({"error": "Invalid sync options."}, HTTPStatus.BAD_REQUEST)
            return

        if automatic:
            if mode != "catchup":
                self.send_json(
                    {"error": "Automatic sync only supports catch-up mode."},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            started, reason, retry_after = start_auto_catchup(max_pages, delay)
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

        if not start_sync_thread(start_page, max_pages, delay, mode):
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

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
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
