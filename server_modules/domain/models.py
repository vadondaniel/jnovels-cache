from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Book:
    title: str
    url: str
    image_url: str | None
    published_at: str | None
    entry_type: str | None = None
    original_post_url: str | None = None
    download_url: str | None = None
    detail_cached_at: str | None = None


@dataclass
class PostDetails:
    entry_type: str
    original_post_url: str | None
    download_url: str | None
    volumes: list["SeriesVolume"] = field(default_factory=list)
    associated_names: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    synopsis: str | None = None
    parent_hints: list[str] = field(default_factory=list)
    subseries_volumes: dict[str, list["SeriesVolume"]] = field(default_factory=dict)


@dataclass
class SeriesVolume:
    label: str
    download_url: str
    position: int
    release_group: str | None = None
    source_list: int = field(default=0, compare=False, repr=False)
