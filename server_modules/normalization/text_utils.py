from __future__ import annotations

import json
import re


def unique_nonempty_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split()).strip(" ,")
        if line and line not in lines:
            lines.append(line)
    return lines


def normalize_synopsis(value: str) -> str | None:
    paragraphs = [
        " ".join(paragraph.split())
        for paragraph in re.split(r"\n\s*\n", value)
        if paragraph.strip()
    ]
    return "\n\n".join(paragraphs) or None


def encode_string_list(values: list[str]) -> str | None:
    return json.dumps(values, ensure_ascii=False) if values else None


def decode_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in decoded if isinstance(item, str)] if isinstance(decoded, list) else []


def normalize_legacy_title_case(value: str) -> str:
    letters = [character for character in value if character.isalpha()]
    if not letters or any(character.islower() for character in letters):
        return value

    minor_words = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "from",
        "in",
        "into",
        "nor",
        "of",
        "on",
        "or",
        "over",
        "per",
        "the",
        "to",
        "up",
        "via",
        "with",
        "yet",
    }
    word_matches = list(re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?", value))
    if not word_matches:
        return value

    first_start = word_matches[0].start()
    last_start = word_matches[-1].start()

    def title_word(match: re.Match[str]) -> str:
        word = match.group(0).casefold()
        if (
            word in minor_words
            and match.start() not in {first_start, last_start}
        ):
            return word
        return word[:1].upper() + word[1:]

    return re.sub(r"[A-Za-z]+(?:['’][A-Za-z]+)?", title_word, value)
