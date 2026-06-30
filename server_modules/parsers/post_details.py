from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any

from server_modules.config import SAO_INDEX_URL
from server_modules.normalization.label_utils import (
    catalog_label_with_subtitle,
    extract_catalog_label,
    extract_sao_volume_label,
    extract_trailing_catalog_label,
    legacy_anchor_label,
    looks_like_download_text,
)
from server_modules.domain.models import PostDetails, SeriesVolume
from server_modules.parsers.post_details_finalizer import build_post_details
from server_modules.normalization.release_groups import (
    catalog_section_from_heading,
    default_catalog_section_from_url,
    is_format_release_group,
    qualify_format_release_group,
    release_group_from_catalog_text,
    release_group_from_heading,
)
from server_modules.normalization.url_utils import (
    canonical_page_url,
    is_external_http_url,
    is_external_site_root,
    is_internal_related_catalog_url,
    is_legacy_download_url,
    is_legacy_forum_landing_url,
)


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
        self._catalog_section: str | None = default_catalog_section_from_url(post_url)
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
        if self._content_depth and not self._list_items and not self._anchor_href:
            section = catalog_section_from_heading(data)
            if section:
                self._catalog_section = section
            release_group = release_group_from_heading(data)
            if release_group:
                self._release_group = qualify_format_release_group(
                    release_group,
                    self._catalog_section,
                )
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

        if release_group_from_heading(text) or looks_like_download_text(text):
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
            parsed_href = urllib.parse.urlparse(href)
            if (
                parsed_href.netloc.casefold() in {"jnovels.com", "www.jnovels.com"}
                and canonical_page_url(href) != canonical_page_url(self.post_url)
            ):
                self.original_post_url = href
            return

        parsed_href = urllib.parse.urlparse(href)
        if is_external_site_root(href):
            return
        genre = text.rstrip(", ")
        if (
            parsed_href.netloc.casefold() in {"jnovels.com", "www.jnovels.com"}
            and "/category/light-novels/" in parsed_href.path.casefold()
            and genre
            and genre not in self._genres
        ):
            self._genres.append(genre)
            return

        if parsed_href.netloc.casefold() in {
            "play.google.com",
            "apps.apple.com",
        }:
            return

        if (
            not self._list_items
            and is_internal_related_catalog_url(href, self.post_url)
        ):
            label = legacy_anchor_label(text)
            if label:
                related_group = "Related manga" if re.search(
                    r"\bManga\b",
                    label,
                    re.IGNORECASE,
                ) else "Related entries"
                self._append_volume(label, href, release_group=related_group)
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
            self._release_group = qualify_format_release_group(
                normalized.upper(),
                self._catalog_section,
            )
        paragraph_label = None
        if self._paragraph_item:
            paragraph_text = " ".join(
                ("".join(self._paragraph_item["parts"]) + " " + text).split()
            )
            paragraph_label = extract_catalog_label(paragraph_text)
        catalog_label = extract_catalog_label(text)
        legacy_download = is_legacy_download_url(href)
        standalone_label = None
        if (
            not self._list_items
            and not paragraph_label
            and legacy_download
            and not is_legacy_forum_landing_url(href)
            and normalized not in {"download", "downloads", "here"}
            and not looks_like_download_text(text)
        ):
            standalone_label = catalog_label or legacy_anchor_label(text)
        if self._list_items and (
            "download" in normalized
            or catalog_label
            or legacy_download
        ):
            self._list_items[-1]["download_url"] = href
        elif self._paragraph_item and not standalone_label and (
            "download" in normalized
            or catalog_label
            or paragraph_label
            or legacy_download
        ):
            self._paragraph_item["download_url"] = href
        if not self.download_url and (
            "download" in normalized
            or catalog_label
            or legacy_download
        ):
            self.download_url = href
        if standalone_label:
            self._append_volume(standalone_label, href)

    def _finish_list_item(self, item: dict[str, Any]) -> None:
        download_url = item["download_url"]
        if not download_url:
            return
        text = " ".join("".join(item["parts"]).split())
        label = None
        if (
            canonical_page_url(self.post_url) == canonical_page_url(SAO_INDEX_URL)
            and item["source_list"] == 1
        ):
            label = extract_sao_volume_label(text)
        label = label or extract_catalog_label(text) or extract_trailing_catalog_label(text)
        if label:
            label = catalog_label_with_subtitle(label, text)
        if not label and is_format_release_group(self._release_group):
            label = "Complete edition"
        if not label and item["source_list"]:
            label = legacy_anchor_label(text)
        if not label and item["source_list"] == 0:
            label = "Complete edition"
        if not label:
            return
        self._append_volume(
            label,
            download_url,
            item["source_list"],
            release_group_from_catalog_text(text, self._release_group),
        )

    def _append_volume(
        self,
        label: str,
        download_url: str,
        source_list: int = 0,
        release_group: str | None = None,
    ) -> None:
        if download_url in self._volume_urls:
            return
        active_release_group = (
            self._release_group
            if release_group is None
            else release_group
        )
        key = (
            source_list,
            (active_release_group or "").casefold(),
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
                active_release_group,
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
                active_release_group,
                source_list,
            )
        )

    def details(self) -> PostDetails:
        return build_post_details(
            post_url=self.post_url,
            original_post_url=self.original_post_url,
            download_url=self.download_url,
            volumes=self.volumes,
            associated_parts=self._associated_parts,
            genres=self._genres,
            description_parts=self._description_parts,
            parent_hints=self._parent_hints,
        )
