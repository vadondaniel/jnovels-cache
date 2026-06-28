from __future__ import annotations

import urllib.parse
from html.parser import HTMLParser

from config import SERIES_INDEX_URL
from models import Book
from url_utils import canonical_page_url


class SeriesIndexParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.entries: list[Book] = []
        self._content_depth = 0
        self._list_depth = 0
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []
        self._seen_urls: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div":
            if self._content_depth:
                self._content_depth += 1
            elif "post-content" in classes:
                self._content_depth = 1
            return
        if not self._content_depth:
            return
        if tag == "ol":
            self._list_depth += 1
        elif tag == "a" and self._list_depth:
            self._anchor_href = attributes.get("href")
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor_href:
            self._finish_anchor()
            self._anchor_href = None
            self._anchor_parts = []
        elif tag == "ol" and self._list_depth:
            self._list_depth -= 1
        elif tag == "div" and self._content_depth:
            self._content_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_parts.append(data)

    def _finish_anchor(self) -> None:
        title = " ".join("".join(self._anchor_parts).split())
        url = canonical_page_url(
            urllib.parse.urljoin(self.page_url, self._anchor_href or "")
        )
        parsed = urllib.parse.urlsplit(url)
        if (
            not title
            or parsed.netloc.casefold() not in {"jnovels.com", "www.jnovels.com"}
            or parsed.path == urllib.parse.urlsplit(SERIES_INDEX_URL).path
            or url in self._seen_urls
        ):
            return
        self._seen_urls.add(url)
        self.entries.append(Book(title, url, None, None))
