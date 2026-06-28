from __future__ import annotations

import urllib.parse


def is_external_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() not in {
        "jnovels.com",
        "www.jnovels.com",
    }


def is_external_site_root(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return is_external_http_url(value) and parsed.path in {"", "/"} and not parsed.query


def is_legacy_download_url(value: str) -> bool:
    host = urllib.parse.urlparse(value).netloc.casefold()
    return (
        host in {"charexempire.com", "www.charexempire.com", "enagato.com", "www.enagato.com"}
        or (
            host in {"mp4directs.com", "www.mp4directs.com"}
            and "/forums/" in urllib.parse.urlparse(value).path.casefold()
        )
    )


def is_legacy_forum_landing_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return (
        parsed.netloc.casefold() in {"mp4directs.com", "www.mp4directs.com"}
        and parsed.path.casefold().startswith("/forums/")
    )


def is_internal_related_catalog_url(value: str, current_url: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    if parsed.netloc.casefold() not in {"jnovels.com", "www.jnovels.com"}:
        return False
    if canonical_page_url(value) == canonical_page_url(current_url):
        return False
    path = parsed.path.casefold()
    return (
        path not in {"", "/", "/faq/"}
        and not path.startswith("/category/")
        and "share=" not in parsed.query
    )


def canonical_page_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", "")
    )


def is_internal_jnovels_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urllib.parse.urlparse(value)
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and parsed.netloc.casefold() in {"jnovels.com", "www.jnovels.com"}
    )


def is_counted_catalog_volume(label: str, download_url: str) -> bool:
    return not is_internal_jnovels_url(download_url)
