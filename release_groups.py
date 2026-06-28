from __future__ import annotations

import re
import urllib.parse

from text_utils import normalize_legacy_title_case


def release_group_from_heading(text: str) -> str | None:
    normalized = " ".join(text.split()).casefold()
    if not normalized:
        return None
    if (
        re.fullmatch(
            r"official(?:\s+(?:translations?|translated versions?|versions?))?",
            normalized,
        )
        or re.fullmatch(
            r"official\s+(?:j[-\s]?novel\s+club|jnc)",
            normalized,
        )
        or re.fullmatch(
            r"(?:j[-\s]?novel\s+club|jnc)\s+"
            r"official\s+(?:translations?|translated versions?|versions?)",
            normalized,
        )
    ):
        if re.search(r"(?:j[-\s]?novel\s+club|jnc)", normalized):
            return "Official J-Novel Club"
        return "Official translation"
    if re.fullmatch(
        r"fan(?:\s+(?:translations?|translated versions?|versions?))?",
        normalized,
    ):
        return "Fan translation"
    if re.fullmatch(r"side stories?", normalized):
        return "Side stories"
    if re.search(r"\bdx\s+series\b", normalized):
        return "DX series"
    if re.fullmatch(
        r"[a-z0-9][a-z0-9 .&'’+\-]{0,80}\s+version"
        r"(?:\s+\([a-z0-9 .&'’+\-]{1,40}\))?",
        normalized,
    ):
        return " ".join(text.split()).strip()
    if re.fullmatch(r"pdf\s+light", normalized):
        return "PDF (light)"
    if re.fullmatch(r"pdf\s+dark", normalized):
        return "PDF (dark)"
    if re.fullmatch(r"pdf", normalized):
        return "PDF"
    if re.fullmatch(r"epub", normalized):
        return "EPUB"
    if re.fullmatch(r"download link\s*[-–—]\s*epub(?: file)?", normalized):
        return "EPUB"
    if re.fullmatch(r"download link\s*[-–—]\s*pdf(?: file)?", normalized):
        return "PDF"
    return None


def catalog_section_from_heading(text: str) -> str | None:
    normalized = " ".join(text.split()).casefold()
    if re.fullmatch(r"light novels?", normalized):
        return "Light novel"
    if re.fullmatch(r"web novels?", normalized):
        return "Web novel"
    return None


def default_catalog_section_from_url(url: str) -> str | None:
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path).casefold()
    if "webnovel" in path or re.search(r"\bweb[-\s]?novel\b", path):
        return "Web novel"
    return None


def qualify_format_release_group(
    release_group: str,
    section: str | None,
) -> str:
    if release_group.casefold() in {"pdf", "epub"} and section in {
        "Light novel",
        "Web novel",
    }:
        return f"{section} {release_group.upper()}"
    return release_group


def release_group_from_catalog_text(
    text: str,
    current_release_group: str | None,
) -> str | None:
    if not re.search(r"\bCrossover\b", text, re.IGNORECASE):
        return current_release_group
    cleaned = " ".join(text.split())
    crossover = re.search(
        r"(?P<name>[A-Za-z0-9][A-Za-z0-9 .&'’:+\-–—]+?)\s+Crossover\b",
        cleaned,
        re.IGNORECASE,
    )
    group = "Crossover"
    if crossover:
        name = crossover.group("name").strip(" -–—:")
        if re.search(r"\s+x\s+", name, re.IGNORECASE):
            name = re.split(r"\s+x\s+", name, flags=re.IGNORECASE)[-1]
        if name:
            group = f"{normalize_legacy_title_case(name)} Crossover"
    format_kind = release_group_format_kind(current_release_group)
    if format_kind:
        return f"{group} {format_kind.upper()}"
    return group


def is_format_release_group(release_group: str | None) -> bool:
    normalized = (release_group or "").casefold()
    return normalized in {
        "pdf",
        "pdf (light)",
        "pdf (dark)",
        "epub",
        "light novel pdf",
        "light novel epub",
        "web novel pdf",
        "web novel epub",
    }


def format_release_family(release_group: str | None) -> str | None:
    normalized = (release_group or "").casefold()
    if not is_format_release_group(normalized):
        return None
    if normalized in {"pdf", "epub"}:
        return ""
    return re.sub(r"\s+(?:pdf|epub)$", "", normalized).strip()


def release_group_format_kind(release_group: str | None) -> str | None:
    normalized = (release_group or "").casefold()
    if re.search(r"\bepub\b", normalized):
        return "epub"
    if re.search(r"\bpdf\b", normalized):
        return "pdf"
    return None
