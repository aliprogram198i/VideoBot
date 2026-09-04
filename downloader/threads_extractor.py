"""Deterministic Threads media discovery from already-fetched HTML.

This module does not execute JavaScript, authenticate, bypass access controls,
or download media. It only identifies publicly exposed media URLs and playback
manifests embedded in a Threads page.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from urllib.parse import urljoin, urlparse


@dataclass(frozen=True)
class ThreadsMedia:
    url: str
    kind: str
    discovered_by: str
    confidence: int


_VIDEO_KEYS = re.compile(
    r"(?:video_url|videoUrl|playback_url|playbackUrl|video_src|videoSrc|"
    r"progressive_url|progressiveUrl|video_versions|videoVersions|"
    r"playback|contentUrl|content_url|embedUrl|embed_url|video)",
    re.IGNORECASE,
)
_MEDIA_URL = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)
_MANIFEST_URL = re.compile(
    r"https?://[^\s\"'<>\\]*(?:\.m3u8|\.mpd)(?:\?[^\s\"'<>\\]*)?",
    re.IGNORECASE,
)


def _decode(value: str) -> str:
    value = unescape(value)
    for old, new in {
        r"\/": "/", r"\u0026": "&", r"\u003d": "=", r"\u003D": "=",
        r"\u003F": "?", r"\u003f": "?", r"\u002f": "/", r"\u002F": "/",
    }.items():
        value = value.replace(old, new)
    return value.strip()


def _absolute(raw: str, page_url: str) -> str | None:
    raw = _decode(raw).rstrip(".,;)")
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"{urlparse(page_url).scheme or 'https'}:{raw}"
    url = urljoin(page_url, raw)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return url


def _kind(url: str) -> str | None:
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mpd"):
        return "dash"
    if path.endswith((".mp4", ".webm", ".mov")):
        return "progressive"
    return None


def _add(
    result: list[ThreadsMedia],
    seen: set[tuple[str, str]],
    url: str,
    kind: str,
    source: str,
    confidence: int,
) -> None:
    key = (url, kind)
    if key in seen:
        return
    seen.add(key)
    result.append(
        ThreadsMedia(
            url=url,
            kind=kind,
            discovered_by=source,
            confidence=confidence,
        )
    )


def _extract_meta_content(page: str) -> list[tuple[str, str]]:
    """Return OpenGraph video values regardless of HTML attribute order."""
    results: list[tuple[str, str]] = []
    for tag in re.finditer(r"<meta\b[^>]*>", page, re.IGNORECASE):
        raw_tag = tag.group(0)
        property_match = re.search(
            r"(?:property|name)\s*=\s*['\"]([^'\"]+)['\"]",
            raw_tag,
            re.IGNORECASE,
        )
        content_match = re.search(
            r"content\s*=\s*['\"]([^'\"]+)['\"]",
            raw_tag,
            re.IGNORECASE,
        )
        if not property_match or not content_match:
            continue
        property_name = property_match.group(1).strip().lower()
        if property_name in {"og:video", "og:video:url", "og:video:secure_url"}:
            results.append((property_name, content_match.group(1)))
    return results


def extract_threads_media(
    page: str,
    page_url: str,
    *,
    max_candidates: int = 50,
) -> list[ThreadsMedia]:
    """Extract publicly exposed Threads video candidates from HTML/JSON text."""
    if not isinstance(page, str):
        raise TypeError("page must be a string")
    parsed_page = urlparse(page_url)
    if parsed_page.scheme not in {"http", "https"} or not parsed_page.hostname:
        raise ValueError("page_url must be an absolute HTTP(S) URL")
    if max_candidates <= 0:
        raise ValueError("max_candidates must be greater than zero")

    page = _decode(page)
    result: list[ThreadsMedia] = []
    seen: set[tuple[str, str]] = set()

    key_pattern = re.compile(
        r"[\"']?([A-Za-z0-9_]*(?:video_url|videoUrl|playback_url|playbackUrl|"
        r"video_src|videoSrc|progressive_url|progressiveUrl|contentUrl|"
        r"content_url|embedUrl|embed_url)[A-Za-z0-9_]*)[\"']?"
        r"\s*[:=]\s*[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    )
    for match in key_pattern.finditer(page):
        url = _absolute(match.group(2), page_url)
        if url:
            _add(
                result,
                seen,
                url,
                _kind(url) or "progressive",
                "threads_video_key",
                125,
            )

    for key_match in _VIDEO_KEYS.finditer(page):
        start = max(0, key_match.start() - 160)
        end = min(len(page), key_match.end() + 900)
        for url_match in _MEDIA_URL.finditer(page[start:end]):
            url = _absolute(url_match.group(0), page_url)
            if not url:
                continue
            kind = _kind(url) or "progressive"
            confidence = 118 if kind in {"hls", "dash"} else 105
            _add(result, seen, url, kind, "threads_embedded_json", confidence)

    for match in _MANIFEST_URL.finditer(page):
        url = _absolute(match.group(0), page_url)
        if url:
            kind = _kind(url)
            if kind:
                _add(result, seen, url, kind, "threads_manifest", 120)

    for _, raw_url in _extract_meta_content(page):
        url = _absolute(raw_url, page_url)
        if url:
            _add(
                result,
                seen,
                url,
                _kind(url) or "progressive",
                "open_graph",
                112,
            )

    result.sort(key=lambda item: (-item.confidence, item.kind, item.url))
    return result[:max_candidates]
