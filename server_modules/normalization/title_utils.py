from __future__ import annotations

import re
import urllib.parse

from server_modules.normalization.label_utils import compact_volume_badge, normalize_volume_number
from server_modules.normalization.text_utils import normalize_legacy_title_case


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
    matches = list(
        re.finditer(
            r"\s+(?:Volume|Vol\.?)\s*"
            r"(Zero|[0-9]+(?:\.[0-9]+)?(?:\s*[-–—]\s*[0-9]+(?:\.[0-9]+)?)?)\b",
            cleaned,
            flags=re.IGNORECASE,
        )
    )
    match = matches[-1] if matches else None
    if match:
        suffix = cleaned[match.end() :]
        has_pdf_suffix = bool(re.search(r"\bPDF\s*$", suffix, re.IGNORECASE))
        series_title = cleaned[: match.start()].rstrip(" ,:;.-–—…")
        if has_pdf_suffix:
            series_title = f"{series_title} PDF"
        return series_title, compact_volume_badge(match.group(1), suffix)

    legacy_number = re.search(
        r"\s+([0-9]+(?:\.[0-9]+)?)_\s*[^/]+$",
        cleaned,
        re.IGNORECASE,
    )
    legacy_slug = re.search(
        r"-([0-9]+(?:\.[0-9]+)?)_-(?:[^/]+-)?(?:epub|pdf)/?$",
        urllib.parse.urlparse(url).path,
        re.IGNORECASE,
    )
    if legacy_number and legacy_slug and legacy_number.group(1) == legacy_slug.group(1):
        return (
            cleaned[: legacy_number.start()].rstrip(" ,:;.-–—…"),
            f"Vol. {normalize_volume_number(legacy_number.group(1))}",
        )

    standalone_part = re.search(
        r"\s+Part\s+([0-9]+(?:\.[0-9]+)?)(?:\s+PDF)?$",
        cleaned,
        re.IGNORECASE,
    )
    if standalone_part:
        return (
            cleaned[: standalone_part.start()].rstrip(" ,:;.-–—…"),
            f"Part {normalize_volume_number(standalone_part.group(1))}",
        )

    bare_number = re.search(
        r"\s+([0-9]+(?:\.[0-9]+)?)(?:\s+(PDF))?$",
        cleaned,
        re.IGNORECASE,
    )
    bare_slug = re.search(
        r"-([0-9]+(?:\.[0-9]+)?)-(?:epub|pdf)/?$",
        urllib.parse.urlparse(url).path,
        re.IGNORECASE,
    )
    if (
        bare_number
        and bare_slug
        and normalize_volume_number(bare_number.group(1))
        == normalize_volume_number(bare_slug.group(1))
    ):
        series_title = cleaned[: bare_number.start()].rstrip(" ,:;.-–—…")
        series_title = re.sub(
            r"\s+Light\s+Novel$",
            "",
            series_title,
            flags=re.IGNORECASE,
        )
        if bare_number.group(2):
            series_title = f"{series_title} PDF"
        return series_title, f"Vol. {normalize_volume_number(bare_number.group(1))}"

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


def virtual_series_url(identity_key: str) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", identity_key.casefold()))
    return f"virtual://jnovels/{slug}"


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


def series_keys_are_related(first: str, second: str) -> bool:
    if not first or not second:
        return False
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    return len(shorter.split()) >= 2 and longer.startswith(f"{shorter} ")


def is_numbered_subseries_title(parent_title: str, candidate_title: str) -> bool:
    parent_key = " ".join(re.findall(r"[a-z0-9]+", parent_title.casefold()))
    candidate_key = " ".join(re.findall(r"[a-z0-9]+", candidate_title.casefold()))
    if not parent_key or not candidate_key.startswith(f"{parent_key} "):
        return False
    suffix = candidate_key[len(parent_key) :].strip()
    return bool(
        re.fullmatch(
            r"(?:year|part|season|arc)\s+[0-9]+(?:\s+(?:part|arc)\s+[0-9]+)?",
            suffix,
        )
    )


def is_named_subseries_title(parent_title: str, candidate_title: str) -> bool:
    parent_key = series_identity_key(parent_title, "")
    candidate_key = series_identity_key(candidate_title, "")
    if not parent_key or not candidate_key.startswith(f"{parent_key} "):
        return False
    suffix = candidate_key[len(parent_key) :].strip()
    return bool(
        re.match(
            r"(?:progressive|alternative\b|short stor(?:y|ies)\b|side stor(?:y|ies)\b|"
            r"zero\b|ex\b|gaiden\b)",
            suffix,
        )
    )


def is_distinct_subseries_title(parent_title: str, candidate_title: str) -> bool:
    return is_numbered_subseries_title(
        parent_title,
        candidate_title,
    ) or is_named_subseries_title(parent_title, candidate_title)


def qualify_numbered_subseries_badge(
    title: str,
    volume_label: str | None,
) -> str | None:
    if not volume_label:
        return None
    match = re.search(
        r"\b(year|part|season|arc)\s+([0-9]+)\b"
        r".*?\b(?:volume|vol\.?)\s*[0-9]",
        title,
        re.IGNORECASE,
    )
    if not match:
        return volume_label
    prefixes = {
        "year": "Yr.",
        "part": "Pt.",
        "season": "S.",
        "arc": "Arc",
    }
    prefix = prefixes[match.group(1).casefold()]
    catalog_volume = re.search(
        r"\b(?:volume|vol\.?)\s*([0-9]+(?:\.[0-9]+)?)",
        volume_label,
        re.IGNORECASE,
    )
    if catalog_volume:
        volume_label = f"Vol. {normalize_volume_number(catalog_volume.group(1))}"
    return f"{prefix} {match.group(2)} · {volume_label}"


def parent_hint_aliases(hints: list[str], url: str) -> list[tuple[str, str]]:
    aliases: list[tuple[str, str]] = []
    seen: set[str] = set()
    for hint in hints:
        for segment in re.split(r"[()]", hint):
            cleaned = re.sub(
                r"^.*?\b(?:download|read)\s+",
                "",
                segment.strip(),
                flags=re.IGNORECASE,
            )
            title, volume_label = parse_book_identity(cleaned, url)
            key = series_identity_key(cleaned, url)
            if (
                not volume_label
                or len(key.split()) < 2
                or key in seen
            ):
                continue
            seen.add(key)
            aliases.append((title, key))
    return aliases
