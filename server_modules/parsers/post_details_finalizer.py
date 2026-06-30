from __future__ import annotations

import re

from server_modules.config import SAO_INDEX_URL
from server_modules.domain.models import PostDetails, SeriesVolume
from server_modules.normalization.text_utils import normalize_synopsis, unique_nonempty_lines
from server_modules.normalization.url_utils import (
    canonical_page_url,
    is_legacy_forum_landing_url,
)


def build_post_details(
    *,
    post_url: str,
    original_post_url: str | None,
    download_url: str | None,
    volumes: list[SeriesVolume],
    associated_parts: list[str],
    genres: list[str],
    description_parts: list[str],
    parent_hints: list[str],
) -> PostDetails:
    entry_type = "volume" if original_post_url else "series"
    associated_names = unique_nonempty_lines("".join(associated_parts))
    synopsis = normalize_synopsis("".join(description_parts))
    selected_volumes = volumes
    source_lists = sorted(
        {volume.source_list for volume in selected_volumes if volume.source_list}
    )
    subseries_volumes: dict[str, list[SeriesVolume]] = {}
    if canonical_page_url(post_url) == canonical_page_url(SAO_INDEX_URL):
        for source_list, identity_key in {
            2: "sword art online progressive",
            3: "sword art online alternative gun gale online",
        }.items():
            catalog = [
                volume for volume in selected_volumes if volume.source_list == source_list
            ]
            if catalog:
                subseries_volumes[identity_key] = [
                    SeriesVolume(
                        volume.label,
                        volume.download_url,
                        position,
                        volume.release_group,
                        volume.source_list,
                    )
                    for position, volume in enumerate(catalog)
                ]
    if len(source_lists) > 1:
        primary_list = source_lists[0]
        selected_volumes = [
            volume
            for volume in selected_volumes
            if volume.source_list in {0, primary_list}
            or volume.release_group is not None
        ]
    if any(
        re.match(r"^(?:Volume|Part)\s+[0-9]", volume.label, re.IGNORECASE)
        for volume in selected_volumes
    ):
        selected_volumes = [
            volume
            for volume in selected_volumes
            if not (
                volume.label.casefold() == "complete edition"
                and is_legacy_forum_landing_url(volume.download_url)
            )
        ]
    selected_download_url = download_url
    if selected_download_url and all(
        volume.download_url != selected_download_url for volume in selected_volumes
    ):
        selected_download_url = selected_volumes[0].download_url if selected_volumes else None
    epub_download = next(
        (
            volume.download_url
            for volume in selected_volumes
            if (volume.release_group or "").casefold().endswith("epub")
        ),
        None,
    )
    if epub_download:
        selected_download_url = epub_download
    return PostDetails(
        entry_type,
        original_post_url,
        selected_download_url,
        selected_volumes if entry_type == "series" else [],
        associated_names,
        genres,
        synopsis,
        parent_hints,
        subseries_volumes,
    )
