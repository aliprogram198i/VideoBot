"""Deterministic Threads media discovery from already-fetched HTML.

This module identifies publicly exposed media URLs from Threads pages. For the
new /share/<id> format, Threads may render the actual post/media data only
through client-side code, so a narrowly scoped dynamic resolver fallback is
used when the normal HTML/JSON discovery produces no candidates.

No authentication, cookie theft, DRM bypass, or access-control bypass is
performed. Resolver URLs are treated as untrusted data and are later validated
by the existing production candidate validator before download.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
import os
import re
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


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
_SHARE_PATH = re.compile(r"/share/([A-Za-z0-9_-]+)(?:/|$)", re.IGNORECASE)

_DEFAULT_DYNAMIC_RESOLVER = "https://fx.akitsuki.me"
_DYNAMIC_RESOLVER_TIMEOUT = 20
_DYNAMIC_RESOLVER_MAX_BYTES = 2 * 1024 * 1024


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


def _extract_json_urls(
    value: object,
    page_url: str,
    result: list[ThreadsMedia],
    seen: set[tuple[str, str]],
    source: str,
    confidence: int,
) -> None:
    """Recursively inspect resolver JSON without assuming one fixed schema."""
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            media_context = bool(
                _VIDEO_KEYS.search(key_text)
                or key_text in {"url", "media_url", "playback_url", "source"}
            )
            if isinstance(child, str) and media_context:
                url = _absolute(child, page_url)
                if url:
                    kind = _kind(url) or "progressive"
                    _add(result, seen, url, kind, source, confidence)
            else:
                _extract_json_urls(
                    child,
                    page_url,
                    result,
                    seen,
                    source,
                    confidence,
                )
    elif isinstance(value, list):
        for item in value:
            _extract_json_urls(
                item,
                page_url,
                result,
                seen,
                source,
                confidence,
            )


def _dynamic_resolver_base() -> str:
    configured = os.getenv("THREADS_DYNAMIC_RESOLVER_URL", "").strip()
    return (configured or _DEFAULT_DYNAMIC_RESOLVER).rstrip("/")


def _fetch_dynamic_share_data(share_id: str) -> tuple[str, object] | None:
    """Fetch the public dynamic resolver JSON for a Threads share ID."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", share_id):
        return None

    base = _dynamic_resolver_base()
    parsed_base = urlparse(base)
    if parsed_base.scheme != "https" or not parsed_base.hostname:
        return None

    endpoint = f"{base}/api/share/{share_id}"
    request = Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "User-Agent": "VideoBot-ThreadsResolver/1.0",
        },
    )

    try:
        with urlopen(request, timeout=_DYNAMIC_RESOLVER_TIMEOUT) as response:
            status = getattr(response, "status", None) or getattr(response, "code", None)
            if status is not None and int(status) >= 400:
                return None
            content_type = response.headers.get_content_type()
            if content_type not in {"application/json", "text/plain"}:
                return None
            body = response.read(_DYNAMIC_RESOLVER_MAX_BYTES + 1)
            if len(body) > _DYNAMIC_RESOLVER_MAX_BYTES:
                return None
            return endpoint, json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None


def _dynamic_share_candidates(page_url: str) -> list[ThreadsMedia]:
    """Resolve /share/<id> through the dynamic public resolver as a last resort."""
    parsed = urlparse(page_url)
    match = _SHARE_PATH.search(parsed.path)
    if not match:
        return []

    share_id = match.group(1)
    fetched = _fetch_dynamic_share_data(share_id)
    if not fetched:
        return []

    endpoint, payload = fetched
    result: list[ThreadsMedia] = []
    seen: set[tuple[str, str]] = set()
    _extract_json_urls(
        payload,
        endpoint,
        result,
        seen,
        "threads_dynamic_resolver",
        130,
    )
    result.sort(key=lambda item: (-item.confidence, item.kind, item.url))
    return result[:50]


def extract_threads_media(
    page: str,
    page_url: str,
    *,
    max_candidates: int = 50,
    source_url: str | None = None,
) -> list[ThreadsMedia]:
    """Extract publicly exposed Threads video candidates from HTML/JSON text."""
    if not isinstance(page, str):
        raise TypeError("page must be a string")
    parsed_page = urlparse(page_url)
    if parsed_page.scheme not in {"http", "https"} or not parsed_page.hostname:
        raise ValueError("page_url must be an absolute HTTP(S) URL")
    if max_candidates <= 0:
        raise ValueError("max_candidates must be greater than zero")

    original_page_url = source_url or page_url
    parsed_source = urlparse(original_page_url)
    if parsed_source.scheme not in {"http", "https"} or not parsed_source.hostname:
        raise ValueError("source_url must be an absolute HTTP(S) URL")

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
            _add(result, seen, url, _kind(url) or "progressive", "threads_video_key", 125)

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
            _add(result, seen, url, _kind(url) or "progressive", "open_graph", 112)

    # The normal resolver is preferred. Only a share link with zero discovered
    # candidates invokes the dynamic resolver, keeping the fallback isolated to
    # the Threads failure mode seen in production.
    if not result:
        for candidate in _dynamic_share_candidates(original_page_url):
            _add(
                result,
                seen,
                candidate.url,
                candidate.kind,
                candidate.discovered_by,
                candidate.confidence,
            )

    result.sort(key=lambda item: (-item.confidence, item.kind, item.url))
    return result[:max_candidates]
