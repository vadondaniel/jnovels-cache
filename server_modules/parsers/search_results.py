from __future__ import annotations

import html
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

from server_modules.config import EXCLUDED_SEARCH_PATHS
from server_modules.domain.models import Book


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
