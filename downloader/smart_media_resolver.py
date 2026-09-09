"""Deterministic media-source resolver used by Smart Search.

This module discovers public video sources from ordinary HTML/player pages
without becoming a downloader. It distinguishes media URLs from player/page
URLs, validates every network hop through the bot's existing SSRF-safe opener,
and returns only sources that have a media signature (MIME type, manifest
signature, or an explicit media extension).

It intentionally does not bypass DRM, authentication, CAPTCHA, signed-access
controls, or browser-only JavaScript execution.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse


MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_CANDIDATES = 40
MAX_EMBED_DEPTH = 2
FETCH_TIMEOUT = 20
MEDIA_PROBE_BYTES = 16 * 1024

_MEDIA_EXTENSIONS = (
    ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".m3u8", ".mpd",
    ".ts", ".m4s", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".wav",
)

_MEDIA_KEYS = {
    "file", "source", "src", "url", "video", "videourl", "contenturl",
    "hls", "dash", "mp4", "stream", "streamurl", "mediaurl",
}
_PAGE_KEYS = {
    "iframe", "embed", "embedurl", "playerurl", "watchurl", "iframeurl",
}
_DATA_MEDIA_RE = re.compile(
    r"\bdata-(?:video-url|stream-url|media-url|source-url|hls|dash)\s*=\s*([\"'])(.*?)\1",
    re.I | re.S,
)
_DATA_PAGE_RE = re.compile(
    r"\bdata-(?:player|player-url|embed|embed-url|iframe|iframe-url)\s*=\s*([\"'])(.*?)\1",
    re.I | re.S,
)
_URL_RE = re.compile(r"https?://[^\"'<>\\\s]+", re.I)


@dataclass(frozen=True)
class MediaCandidate:
    url: str
    score: int
    kind: str
    source_page: str


def _decode(value: str) -> str:
    value = html_lib.unescape(value.strip())
    value = value.replace("\\/", "/")
    value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), value)
    value = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), value)
    value = value.replace("\\\"", '"').replace("\\'", "'")
    return value.strip()


def _absolute(value: str, base: str) -> str | None:
    value = _decode(value).strip()
    if value.startswith(("//", "/")):
        value = urljoin(base, value)
    elif not value.lower().startswith(("http://", "https://")):
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _looks_media_url(value: str) -> bool:
    path = urlparse(value).path.lower()
    return path.endswith(_MEDIA_EXTENSIONS)


def _is_page_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _PAGE_KEYS


def _is_media_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _MEDIA_KEYS


def _collect_from_json(value, base: str, media: list[tuple[str, int]], pages: list[str], depth: int = 0) -> None:
    if depth > 5:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(item, (str, dict, list)):
                continue
            if isinstance(item, str):
                candidate = _absolute(item, base)
                if candidate:
                    if _is_media_key(key):
                        media.append((candidate, 90))
                    elif _is_page_key(key):
                        pages.append(candidate)
                    elif _looks_media_url(candidate):
                        media.append((candidate, 80))
            _collect_from_json(item, base, media, pages, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _collect_from_json(item, base, media, pages, depth + 1)


def _extract(page: str, source_url: str) -> tuple[list[tuple[str, int]], list[str]]:
    media: list[tuple[str, int]] = []
    pages: list[str] = []

    for match in _DATA_MEDIA_RE.finditer(page):
        candidate = _absolute(match.group(2), source_url)
        if candidate:
            media.append((candidate, 92))
    for match in _DATA_PAGE_RE.finditer(page):
        candidate = _absolute(match.group(2), source_url)
        if candidate:
            pages.append(candidate)

    # HTML attributes. iframe/embed are always page candidates, never media.
    for tag_match in re.finditer(r"<(?:video|source|iframe|embed)\b[^>]*>", page, re.I):
        tag = tag_match.group(0)
        tag_name = re.match(r"<([a-z0-9]+)", tag, re.I)
        name = tag_name.group(1).casefold() if tag_name else ""
        for attr in re.finditer(r"\b(?:src|srcset|data-src|data-url|data-file|data-video|data-stream)\s*=\s*([\"'])(.*?)\1", tag, re.I | re.S):
            candidate = _absolute(attr.group(2), source_url)
            if not candidate:
                continue
            if name in {"iframe", "embed"}:
                pages.append(candidate)
            else:
                media.append((candidate, 96))

    # OpenGraph/Twitter media metadata.
    for match in re.finditer(r"<meta\b[^>]*(?:property|name)\s*=\s*([\"'])([^\"']+)\1[^>]*content\s*=\s*([\"'])(.*?)\3[^>]*>", page, re.I | re.S):
        key = match.group(2).casefold()
        candidate = _absolute(match.group(4), source_url)
        if not candidate:
            continue
        if key in {"og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"}:
            media.append((candidate, 82))
        elif key in {"og:video:iframe", "twitter:player"}:
            pages.append(candidate)

    # JSON-LD and common JavaScript configuration objects.
    for match in re.finditer(r"<script\b[^>]*type\s*=\s*([\"'])application/ld\+json\1[^>]*>(.*?)</script>", page, re.I | re.S):
        try:
            payload = json.loads(html_lib.unescape(match.group(2)))
        except (TypeError, ValueError):
            continue
        _collect_from_json(payload, source_url, media, pages)

    # Common JS player properties. Keep page/player properties separate.
    for match in re.finditer(r"\b(?:file|source|src|url|videoUrl|contentUrl|hls|dash|mp4|streamUrl)\s*[:=]\s*[\"'](.*?)[\"']", page, re.I | re.S):
        candidate = _absolute(match.group(1), source_url)
        if candidate:
            media.append((candidate, 88 if _looks_media_url(candidate) else 78))
    for match in re.finditer(r"\b(?:embedUrl|playerUrl|watchUrl|iframeUrl)\s*[:=]\s*[\"'](.*?)[\"']", page, re.I | re.S):
        candidate = _absolute(match.group(1), source_url)
        if candidate:
            pages.append(candidate)

    # Last-resort explicit media URLs visible in page source.
    for raw in _URL_RE.findall(page):
        candidate = _absolute(raw, source_url)
        if candidate and _looks_media_url(candidate):
            media.append((candidate, 70))

    return media, pages


def _probe(
    url: str,
    *,
    validator: Callable[[str], object],
    request_factory: Callable[..., object],
    open_function: Callable[..., object],
) -> tuple[bool, int]:
    try:
        validator(url)
        request = request_factory(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AliBot Smart Search)"})
        response = open_function(request, timeout=FETCH_TIMEOUT, max_bytes=MEDIA_PROBE_BYTES)
        try:
            content_type = (response.headers.get_content_type() or "").casefold()
            content = response.read(MEDIA_PROBE_BYTES)
        finally:
            response.close()
    except Exception:
        return False, 0

    body = content.lstrip()
    if "mpegurl" in content_type or "vnd.apple.mpegurl" in content_type or body.startswith(b"#EXTM3U"):
        return True, 100
    if "dash+xml" in content_type or (b"<MPD" in body[:2048] or b":MPD" in body[:2048]):
        return True, 98
    if content_type.startswith("video/"):
        return True, 96
    if content_type.startswith("audio/"):
        return True, 94
    if _looks_media_url(url):
        return True, 86
    return False, 0


def resolve(
    url: str,
    *,
    validator: Callable[[str], object],
    request_factory: Callable[..., object],
    open_function: Callable[..., object],
    read_function: Callable[[object, int], bytes],
) -> list[str]:
    """Return validated media URLs discovered from a public page."""
    try:
        validator(url)
    except Exception:
        return []

    queue: list[tuple[str, int]] = [(url, 0)]
    visited: set[str] = set()
    candidates: dict[str, MediaCandidate] = {}

    while queue and len(visited) < 8:
        page_url, depth = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        try:
            validator(page_url)
            request = request_factory(
                page_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AliBot Smart Search)"},
            )
            response = open_function(request, timeout=FETCH_TIMEOUT, max_bytes=MAX_PAGE_BYTES)
            try:
                content_type = (response.headers.get_content_type() or "").casefold()
                body = read_function(response, MAX_PAGE_BYTES)
            finally:
                response.close()
        except Exception:
            continue

        if content_type.startswith(("video/", "audio/")) or body.lstrip().startswith(b"#EXTM3U"):
            ok, score = _probe(page_url, validator=validator, request_factory=request_factory, open_function=open_function)
            if ok:
                candidates[page_url] = MediaCandidate(page_url, score + 20, "direct", url)
            continue

        text = body.decode("utf-8", errors="ignore")
        media, pages = _extract(text, page_url)

        for candidate, score in media[:MAX_CANDIDATES]:
            if len(candidates) >= MAX_CANDIDATES:
                break
            ok, probe_score = _probe(candidate, validator=validator, request_factory=request_factory, open_function=open_function)
            if ok:
                candidates[candidate] = MediaCandidate(candidate, score + probe_score, "media", page_url)

        if depth < MAX_EMBED_DEPTH:
            for candidate in pages:
                try:
                    validator(candidate)
                except Exception:
                    continue
                if candidate not in visited:
                    queue.append((candidate, depth + 1))

    ranked = sorted(candidates.values(), key=lambda item: (-item.score, item.url))
    return [item.url for item in ranked[:8]]


def install(bot_module) -> None:
    """Install Smart Search's resolver as a conservative direct-fallback overlay."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_smart_search_resolver", False):
        return

    def wrapped(url, *args, **kwargs):
        # Preserve the existing extractor as the first authority. The new
        # resolver only runs when the established fallback found nothing.
        try:
            existing = original(url, *args, **kwargs)
        except Exception:
            existing = []
        if existing:
            return existing

        try:
            resolved = resolve(
                url,
                validator=bot_module.validate_public_http_url,
                request_factory=bot_module.Request,
                open_function=bot_module.safe_urlopen,
                read_function=bot_module.read_limited,
            )
            if resolved:
                print(f"🔎 Smart Search Resolver: validated {len(resolved)} media candidate(s)", flush=True)
                return resolved
        except Exception as exc:
            print(f"⚠️ Smart Search Resolver skipped: {type(exc).__name__}", flush=True)
        return existing

    wrapped._smart_search_resolver = True
    bot_module.extract_direct_media_urls = wrapped
    print("🔎 Smart Search Resolver: ENABLED", flush=True)
