from __future__ import annotations

import re

from server_modules.normalization.text_utils import normalize_legacy_title_case
from server_modules.normalization.volume_numbers import (
    normalize_volume_designator,
    normalize_volume_number,
    roman_to_integer,
)


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
