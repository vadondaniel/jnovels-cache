from __future__ import annotations

import urllib.error
import urllib.request
from http import HTTPStatus

from server_modules.config import BASE_URL, SERIES_INDEX_URL, USER_AGENT
from server_modules.domain.models import Book, PostDetails
from server_modules.parsers.post_details import PostDetailsParser
from server_modules.parsers.search_results import SearchResultsParser
from server_modules.parsers.series_index import SeriesIndexParser


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


def fetch_series_index(timeout: int = 30) -> list[Book]:
    request = urllib.request.Request(
        SERIES_INDEX_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
    parser = SeriesIndexParser(SERIES_INDEX_URL)
    parser.feed(body)
    if not parser.entries:
        raise ValueError("The JNovels series index did not contain any entries.")
    return parser.entries


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
