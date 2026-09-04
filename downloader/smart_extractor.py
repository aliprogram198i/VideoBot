"""Deterministic smart media-source extraction primitives.

This module only discovers candidate URLs from HTML.
It does not download media and does not bypass authentication,
DRM, or other access controls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
import re
from typing import Any
from urllib.parse import urljoin, urlparse


_MEDIA_EXTENSIONS = {
    ".m3u8": "hls",
    ".mpd": "dash",
    ".mp4": "progressive",
    ".webm": "progressive",
    ".mov": "progressive",
}

_MEDIA_TYPES = {"hls", "dash", "progressive"}
_IFRAME_TYPE = "iframe"


@dataclass(frozen=True)
class MediaCandidate:
    """A normalized URL candidate discovered in a page."""

    url: str
    kind: str
    source_page: str
    discovered_by: str
    depth: int = 0
    score: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def _decode_text(value: str) -> str:
    """Decode common HTML/JavaScript URL escaping without executing JS."""
    value = unescape(value)

    replacements = {
        r"\/": "/",
        r"\u0026": "&",
        r"\u003d": "=",
        r"\u003D": "=",
        r"\u003F": "?",
        r"\u003f": "?",
        r"\u002f": "/",
        r"\u002F": "/",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    return value.strip()


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _kind_from_url(url: str) -> str | None:
    path = urlparse(url).path.lower()

    for extension, kind in _MEDIA_EXTENSIONS.items():
        if path.endswith(extension):
            return kind

    return None


def _score(kind: str, discovered_by: str) -> int:
    """Initial deterministic score; later phases can make this configurable."""
    base = {
        "hls": 100,
        "dash": 95,
        "progressive": 90,
        "iframe": 50,
    }.get(kind, 0)

    bonus = {
        "video": 15,
        "source": 12,
        "script": 5,
        "iframe": 0,
        "attribute": 3,
    }.get(discovered_by, 0)

    return base + bonus


def _normalize_candidate_url(raw_url: str, page_url: str) -> str | None:
    raw_url = _decode_text(raw_url)

    if not raw_url:
        return None

    if raw_url.startswith("//"):
        scheme = urlparse(page_url).scheme or "https"
        raw_url = f"{scheme}:{raw_url}"

    absolute = urljoin(page_url, raw_url)

    if not _is_http_url(absolute):
        return None

    return absolute


def _add_candidate(
    candidates: list[MediaCandidate],
    seen: set[tuple[str, str]],
    raw_url: str,
    *,
    page_url: str,
    discovered_by: str,
    depth: int,
) -> None:
    normalized = _normalize_candidate_url(raw_url, page_url)

    if not normalized:
        return

    kind = _kind_from_url(normalized)

    # An iframe is a discovery candidate. It is deliberately not treated
    # as a final media source until a later resolver analyzes its page.
    if discovered_by == "iframe":
        kind = _IFRAME_TYPE

    if kind not in _MEDIA_TYPES and kind != _IFRAME_TYPE:
        return

    key = (normalized, kind)

    if key in seen:
        return

    seen.add(key)

    candidates.append(
        MediaCandidate(
            url=normalized,
            kind=kind,
            source_page=page_url,
            discovered_by=discovered_by,
            depth=depth,
            score=_score(kind, discovered_by),
        )
    )


def _extract_attribute_values(page: str, attribute: str) -> list[str]:
    pattern = rf"""(?:{re.escape(attribute)})\s*=\s*["']([^"']+)["']"""
    return re.findall(pattern, page, flags=re.IGNORECASE)


def _extract_tag_attribute_values(
    page: str,
    tag: str,
    attribute: str,
) -> list[str]:
    pattern = (
        rf"<{re.escape(tag)}\b[^>]*?"
        rf"{re.escape(attribute)}\s*=\s*['\"]([^'\"]+)['\"]"
        rf"[^>]*>"
    )
    return re.findall(pattern, page, flags=re.IGNORECASE)


def _extract_media_urls_from_text(page: str) -> list[str]:
    """Find explicit media URLs without interpreting or executing JavaScript."""
    results: list[str] = []

    escaped_or_plain = re.compile(
        r"""(?P<quote>["'])(?P<url>[^"']+?\.(?:m3u8|mpd|mp4|webm|mov)(?:\?[^"']*)?)(?P=quote)""",
        flags=re.IGNORECASE,
    )

    for match in escaped_or_plain.finditer(page):
        results.append(match.group("url"))

    absolute = re.compile(
        r"""https?://[^\s"'<>\\]+?\.(?:m3u8|mpd|mp4|webm|mov)(?:\?[^\s"'<>\\]*)?""",
        flags=re.IGNORECASE,
    )

    results.extend(absolute.findall(page))

    return results


def extract_candidates(
    page: str,
    page_url: str,
    *,
    depth: int = 0,
    max_candidates: int = 100,
) -> list[MediaCandidate]:
    """Extract deterministic media/embed candidates from already-fetched HTML.

    No network requests are made by this function.
    """
    if not isinstance(page, str):
        raise TypeError("page must be a string")

    if not _is_http_url(page_url):
        raise ValueError("page_url must be an absolute HTTP(S) URL")

    page = _decode_text(page)

    candidates: list[MediaCandidate] = []
    seen: set[tuple[str, str]] = set()

    # Explicit media elements.
    for tag, attribute, source_name in (
        ("video", "src", "video"),
        ("source", "src", "source"),
    ):
        for value in _extract_tag_attribute_values(page, tag, attribute):
            _add_candidate(
                candidates,
                seen,
                value,
                page_url=page_url,
                discovered_by=source_name,
                depth=depth,
            )

    # Common lazy-loading/player attributes.
    for attribute in (
        "data-src",
        "data-url",
        "data-video",
        "data-file",
        "data-source",
        "data-hls",
        "data-dash",
    ):
        for value in _extract_attribute_values(page, attribute):
            _add_candidate(
                candidates,
                seen,
                value,
                page_url=page_url,
                discovered_by="attribute",
                depth=depth,
            )

    # Explicit embed-page attributes are treated as iframe candidates so
    # EmbedResolver can recursively inspect the referenced page.
    for value in _extract_attribute_values(page, "data-embed-url"):
        _add_candidate(
            candidates,
            seen,
            value,
            page_url=page_url,
            discovered_by="iframe",
            depth=depth,
        )

    # Iframes/embeds are discovered separately for later recursive resolution.
    for tag in ("iframe", "embed"):
        for value in _extract_tag_attribute_values(page, tag, "src"):
            _add_candidate(
                candidates,
                seen,
                value,
                page_url=page_url,
                discovered_by="iframe",
                depth=depth,
            )

    # Explicit media URLs inside scripts/configuration text.
    for value in _extract_media_urls_from_text(page):
        _add_candidate(
            candidates,
            seen,
            value,
            page_url=page_url,
            discovered_by="script",
            depth=depth,
        )

    # Lightweight JSON scan. This intentionally parses only standalone JSON
    # objects/arrays that are already present in the HTML; it never executes JS.
    for match in re.finditer(r"""["'](?:file|source|url|src|hls|dash)["']\s*:\s*["']([^"']+)["']""", page, flags=re.IGNORECASE):
        _add_candidate(
            candidates,
            seen,
            match.group(1),
            page_url=page_url,
            discovered_by="script",
            depth=depth,
        )

    # Higher confidence first, then deterministic URL ordering.
    candidates.sort(key=lambda item: (-item.score, item.url))

    return candidates[:max_candidates]


def extract_candidate_urls(
    page: str,
    page_url: str,
    *,
    depth: int = 0,
    max_candidates: int = 100,
) -> list[str]:
    """Compatibility helper returning only candidate URLs."""
    return [
        candidate.url
        for candidate in extract_candidates(
            page,
            page_url,
            depth=depth,
            max_candidates=max_candidates,
        )
    ]
