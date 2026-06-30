from __future__ import annotations

import re


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
