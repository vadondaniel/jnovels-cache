from __future__ import annotations

import re

from server_modules.normalization.volume_numbers import normalize_volume_number


def identity_key_from_text(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.casefold()))


def spaced_identity_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def series_keys_are_related(first: str, second: str) -> bool:
    if not first or not second:
        return False
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    return len(shorter.split()) >= 2 and longer.startswith(f"{shorter} ")


def is_numbered_subseries_title(parent_title: str, candidate_title: str) -> bool:
    parent_key = spaced_identity_key(parent_title)
    candidate_key = spaced_identity_key(candidate_title)
    if not parent_key or not candidate_key.startswith(f"{parent_key} "):
        return False
    suffix = candidate_key[len(parent_key) :].strip()
    return bool(
        re.fullmatch(
            r"(?:year|part|season|arc)\s+[0-9]+(?:\s+(?:part|arc)\s+[0-9]+)?",
            suffix,
        )
    )


def is_named_subseries_title(
    parent_title: str,
    candidate_title: str,
    *,
    series_identity_key,
) -> bool:
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


def is_distinct_subseries_title(
    parent_title: str,
    candidate_title: str,
    *,
    series_identity_key,
) -> bool:
    return is_numbered_subseries_title(
        parent_title,
        candidate_title,
    ) or is_named_subseries_title(
        parent_title,
        candidate_title,
        series_identity_key=series_identity_key,
    )


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
