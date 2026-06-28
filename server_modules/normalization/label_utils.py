from __future__ import annotations

import re

from server_modules.normalization.text_utils import normalize_legacy_title_case


def catalog_display_label(
    label: str,
    series_title: str | None,
    release_group: str | None,
) -> str:
    if not series_title or (release_group or "").casefold() != "side stories":
        return label
    stripped = re.sub(
        rf"^{re.escape(series_title)}\s*[-–—:]\s*",
        "",
        label,
        flags=re.IGNORECASE,
    ).strip()
    return stripped or label


def catalog_label_with_subtitle(label: str, text: str) -> str:
    match = re.search(
        r"\b(?:Volume|Vol\.?)\s*"
        r"(Zero|[0-9]+(?:\.[0-9]+)?)\b(?P<tail>.*)$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return label
    volume_label = f"Volume {normalize_volume_number(match.group(1))}"
    if volume_label.casefold() != label.casefold():
        return label
    subtitle = re.sub(
        r"\b(?:PDF|EPUB|DOWNLOAD|HERE|OPEN|READ|FILE)\b",
        " ",
        match.group("tail"),
        flags=re.IGNORECASE,
    )
    subtitle = " ".join(subtitle.strip(" -–—.:|�\u00a0").split())
    if not subtitle or extract_catalog_label(subtitle) == label:
        return label
    return f"{label} - {normalize_legacy_title_case(subtitle)}"

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


def is_volume_label(value: str) -> bool:
    return extract_volume_label(value) is not None


def extract_volume_label(value: str) -> str | None:
    match = re.search(
        r"\b(?:"
        r"(?:(YEAR|PART)\s*([0-9]+))"
        r"|(?:([0-9]+)(?:ST|ND|RD|TH)\s+YEAR)"
        r"|(?:YR\s*([0-9]+))"
        r")?\s*"
        r"(?:VOLUME|VOLUMES|VOL\.?)\s*"
        r"(ZERO|[0-9]+(?:\.[0-9]+)?(?:\s*[-–—&]\s*[0-9]+(?:\.[0-9]+)?)?)"
        r"(\+*)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    number = normalize_volume_designator(match.group(5))
    suffix = match.group(6)
    trailing = value[match.end() :]
    canto_match = re.match(
        r"\s+(CANTO\s+)?([0-9]+|[IVX]+)\b",
        trailing,
        re.IGNORECASE,
    )
    book_match = re.match(r"\s*\(\s*BOOK\s+([0-9]+)\s*\)", trailing, re.IGNORECASE)
    part_match = re.match(
        r"\s*(?:[-–—,]\s*)?(PART|ACT)\s*([0-9]+|[IVX]+)\b",
        trailing,
        re.IGNORECASE,
    )
    chapter_match = re.match(
        r"\s*(?:[-–—,:]\s*)?CHAPTER\s*([0-9]+)\b",
        trailing,
        re.IGNORECASE,
    )
    named_half_match = re.match(
        r"\s+(EXORDIUM|FINALE|PROLOGUE|AFTERWORD)\b",
        trailing,
        re.IGNORECASE,
    )
    qualifier = ""
    if part_match:
        part = part_match.group(2)
        part_number = (
            normalize_volume_number(part)
            if part.isdecimal()
            else str(roman_to_integer(part) or part.upper())
        )
        qualifier = (
            f" {part_match.group(1).title()} "
            f"{part_number}"
        )
    elif canto_match:
        canto_prefix = "Canto " if canto_match.group(1) else ""
        qualifier = f" {canto_prefix}{canto_match.group(2).upper()}"
    elif chapter_match:
        qualifier = f" Chapter {normalize_volume_number(chapter_match.group(1))}"
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
    crossover = re.search(
        r"\bCrossover\b\s*[-–—:]*\s*"
        r"(?:\bPART\s+([0-9]+(?:\.[0-9]+)?)\b)?\s*(.*)$",
        cleaned,
        re.IGNORECASE,
    )
    if crossover:
        part_number = crossover.group(1)
        subtitle = re.sub(
            r"^(?:[-–—:]\s*)?(?:PART\s+[0-9]+(?:\.[0-9]+)?\s*)?",
            "",
            crossover.group(2),
            flags=re.IGNORECASE,
        ).strip(" -–—.:|")
        if part_number:
            label = f"Part {normalize_volume_number(part_number)}"
            if subtitle:
                label = f"{label} - {subtitle}"
            return normalize_legacy_title_case(label)
        return "Crossover"
    standalone_part = re.search(
        r"\b(PART)\s+([0-9]+(?:\.[0-9]+)?)\b",
        cleaned,
        re.IGNORECASE,
    )
    if standalone_part:
        return f"Part {normalize_volume_number(standalone_part.group(2))}"
    match = re.search(
        r"\b(SIDE STORIES|DISC EX|GAIDEN|AFTER STORY)\b",
        cleaned,
        re.IGNORECASE,
    )
    return match.group(1).title() if match else None


def extract_catalog_label(value: str) -> str | None:
    revised = re.search(
        r"\bVolumes?\s+([0-9]+(?:\.[0-9]+)?)\s*(?:&|and)\s*"
        r"([0-9]+(?:\.[0-9]+)?).*?Revised Editions?\b",
        value,
        re.IGNORECASE,
    )
    if revised:
        return f"Volume {normalize_volume_number(revised.group(1))}"
    label = extract_volume_label(value)
    if label:
        return label
    legacy = re.search(r"\s([0-9]+(?:\.[0-9]+)?)_\s*\S", value)
    if legacy:
        return f"Volume {normalize_volume_number(legacy.group(1))}"
    return extract_named_release_label(value)


def extract_sao_volume_label(value: str) -> str | None:
    cleaned = re.sub(r"\bDOWNLOAD\b\s*$", "", value, flags=re.IGNORECASE)
    cleaned = cleaned.rstrip(" -–—.:|")
    match = re.search(
        r"\bSword Art Online\s+([0-9]+(?:\.[0-9]+)?)\s*_\s*(.+)$",
        cleaned,
        re.IGNORECASE,
    )
    if not match:
        return None
    subtitle = match.group(2).strip(" -–—.:|_")
    if not subtitle:
        return None
    number = normalize_volume_number(match.group(1))
    return f"Volume {number} — {subtitle}"


def extract_trailing_catalog_label(value: str) -> str | None:
    without_action = re.sub(r"\bDOWNLOAD\b", "", value, flags=re.IGNORECASE)
    cleaned = without_action.rstrip(" -–—.:|")
    match = re.search(r"\b([0-9]+(?:\.[0-9]+)?)(\+*)$", cleaned)
    if not match:
        return None
    return f"Volume {normalize_volume_number(match.group(1))}{match.group(2)}"


def normalize_volume_number(value: str) -> str:
    if value.casefold() == "zero":
        return "0"
    whole, separator, fraction = value.partition(".")
    normalized_whole = str(int(whole))
    return f"{normalized_whole}.{fraction}" if separator else normalized_whole


def normalize_volume_designator(value: str) -> str:
    parts = re.split(r"\s*[-–—&]\s*", value, maxsplit=1)
    if len(parts) == 2:
        return (
            f"{normalize_volume_number(parts[0])}"
            f"–{normalize_volume_number(parts[1])}"
        )
    return normalize_volume_number(value)


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
    normalized_number = normalize_volume_designator(number)
    qualifier = re.search(
        r"(?:^\s*[,\-:–—]?\s*|,\s*)"
        r"(Part|Act|Canto)\s+([0-9]+|[IVX]+)\b",
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
    chapter = re.search(r"\bChapter\s+([0-9]+)\b", suffix, re.IGNORECASE)
    if chapter:
        return f"Vol. {normalized_number} Ch. {normalize_volume_number(chapter.group(1))}"
    named = re.search(
        r"\b(Exordium|Finale|Prologue|Afterword)\b",
        suffix,
        re.IGNORECASE,
    )
    if named:
        return f"Vol. {normalized_number} {named.group(1).title()}"
    return f"Vol. {normalized_number}"


def compact_catalog_volume_badge(label: str | None) -> str | None:
    if not label:
        return None
    match = re.search(
        r"\b(?:Volume|Vol\.?)\s*"
        r"(Zero|[0-9]+(?:\.[0-9]+)?(?:\s*[-–—]\s*[0-9]+(?:\.[0-9]+)?)?)\b",
        label,
        re.IGNORECASE,
    )
    if not match:
        return extract_named_release_label(label)
    return compact_volume_badge(match.group(1), label[match.end() :])


def prefer_catalog_volume_badge(
    parsed_badge: str | None,
    catalog_label: str | None,
) -> str | None:
    catalog_badge = compact_catalog_volume_badge(catalog_label)
    if not parsed_badge:
        return catalog_badge
    if not catalog_badge:
        return parsed_badge
    parsed_plain = re.fullmatch(r"Vol\. ([0-9]+)", parsed_badge)
    catalog_volume = re.match(r"Vol\. ([0-9]+)(?:\.|\s)", catalog_badge)
    if (
        parsed_plain
        and catalog_volume
        and parsed_plain.group(1) == catalog_volume.group(1)
        and catalog_badge != parsed_badge
    ):
        return catalog_badge
    return parsed_badge
