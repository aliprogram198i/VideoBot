"""Deterministic public-media resolver used by Smart Search.

The resolver is deliberately a discovery layer, not a DRM/auth/CAPTCHA bypass.
It walks public article/player pages, separates page/player URLs from actual
media URLs, understands common HTML/JSON/JS player configurations, and can
ask the already-installed yt-dlp extractor to resolve a public player page.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse

MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_CANDIDATES = 48
MAX_EMBED_DEPTH = 3
MAX_PAGES = 12
FETCH_TIMEOUT = 20
MEDIA_PROBE_BYTES = 16 * 1024
YTDLP_TIMEOUT = 30

_MEDIA_EXTENSIONS = (
    ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".m3u8", ".mpd",
    ".ts", ".m4s", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".wav",
)
_MEDIA_KEYS = {
    "file", "source", "src", "url", "video", "videourl", "contenturl",
    "hls", "dash", "mp4", "stream", "streamurl", "mediaurl", "playbackurl",
    "playlist", "manifest", "manifesturl", "video_src", "video_source",
}
_PAGE_KEYS = {
    "iframe", "embed", "embedurl", "playerurl", "watchurl", "iframeurl",
    "player", "playerpage", "watchpage",
}
_DATA_MEDIA_RE = re.compile(
    r"\bdata-(?:video-url|stream-url|media-url|source-url|hls|dash|file|src)\s*=\s*([\"'])(.*?)\1",
    re.I | re.S,
)
_DATA_PAGE_RE = re.compile(
    r"\bdata-(?:player|player-url|embed|embed-url|iframe|iframe-url|watch-url)\s*=\s*([\"'])(.*?)\1",
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
    value = html_lib.unescape(value.strip()).replace("\\/", "/")
    value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), value)
    value = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), value)
    value = value.replace('\\"', '"').replace("\\'", "'")
    return value.strip()


def _absolute(value: str, base: str) -> str | None:
    value = _decode(value)
    if value.startswith(("//", "/")):
        value = urljoin(base, value)
    if not value.lower().startswith(("http://", "https://")):
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _looks_media_url(value: str) -> bool:
    return urlparse(value).path.lower().endswith(_MEDIA_EXTENSIONS)


def _key_name(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _is_page_key(key: str) -> bool:
    return _key_name(key) in {_key_name(x) for x in _PAGE_KEYS}


def _is_media_key(key: str) -> bool:
    return _key_name(key) in {_key_name(x) for x in _MEDIA_KEYS}


def _collect_from_json(value, base: str, media: list[tuple[str, int]], pages: list[str], depth: int = 0) -> None:
    if depth > 7:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                candidate = _absolute(item, base)
                if candidate:
                    if _is_page_key(key):
                        pages.append(candidate)
                    elif _is_media_key(key):
                        media.append((candidate, 94))
                    elif _looks_media_url(candidate):
                        media.append((candidate, 82))
            elif isinstance(item, (dict, list)):
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
            media.append((candidate, 93))
    for match in _DATA_PAGE_RE.finditer(page):
        candidate = _absolute(match.group(2), source_url)
        if candidate:
            pages.append(candidate)

    # HTML player elements: iframe/embed are NEVER media candidates.
    for tag_match in re.finditer(r"<(?:video|source|iframe|embed)\b[^>]*>", page, re.I):
        tag = tag_match.group(0)
        name_match = re.match(r"<([a-z0-9]+)", tag, re.I)
        name = name_match.group(1).casefold() if name_match else ""
        for attr in re.finditer(
            r"\b(?:src|srcset|data-src|data-url|data-file|data-video|data-stream)\s*=\s*([\"'])(.*?)\1",
            tag, re.I | re.S,
        ):
            candidate = _absolute(attr.group(2).split(",", 1)[0], source_url)
            if not candidate:
                continue
            if name in {"iframe", "embed"}:
                pages.append(candidate)
            else:
                media.append((candidate, 97))

    # OpenGraph/Twitter player metadata.
    for match in re.finditer(
        r"<meta\b[^>]*(?:property|name)\s*=\s*([\"'])([^\"']+)\1[^>]*content\s*=\s*([\"'])(.*?)\3[^>]*>",
        page, re.I | re.S,
    ):
        key = match.group(2).casefold()
        candidate = _absolute(match.group(4), source_url)
        if not candidate:
            continue
        if key in {"og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"}:
            media.append((candidate, 84))
        elif key in {"og:video:iframe", "twitter:player"}:
            pages.append(candidate)

    scripts = [m.group(1) for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", page, re.I | re.S)]
    for script in scripts:
        for match in re.finditer(
            r"\b(?:file|source|src|url|videoUrl|contentUrl|hls|dash|mp4|streamUrl|manifest|playbackUrl)\s*[:=]\s*([\"'])(.*?)\1",
            script, re.I | re.S,
        ):
            candidate = _absolute(match.group(2), source_url)
            if candidate:
                media.append((candidate, 91 if _looks_media_url(candidate) else 80))
        for match in re.finditer(
            r"\b(?:embedUrl|playerUrl|watchUrl|iframeUrl|playerPage|watchPage)\s*[:=]\s*([\"'])(.*?)\1",
            script, re.I | re.S,
        ):
            candidate = _absolute(match.group(2), source_url)
            if candidate:
                pages.append(candidate)

        # Parse complete JSON objects embedded in scripts when possible.
        for blob in re.findall(r"\{.*?\}", script, re.S):
            if not any(token in blob.casefold() for token in ("m3u8", "mp4", "source", "iframe", "player")):
                continue
            try:
                payload = json.loads(blob)
            except (TypeError, ValueError):
                continue
            _collect_from_json(payload, source_url, media, pages)

    for raw in _URL_RE.findall(page):
        candidate = _absolute(raw, source_url)
        if candidate and _looks_media_url(candidate):
            media.append((candidate, 72))

    return media, pages


def _probe(
    url: str,
    *,
    validator: Callable[[str], object],
    request_factory: Callable[..., object],
    open_function: Callable[..., object],
    referer: str | None = None,
) -> tuple[bool, int]:
    try:
        validator(url)
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AliBot Smart Search)"}
        if referer:
            headers["Referer"] = referer
        request = request_factory(url, headers=headers)
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
    if "dash+xml" in content_type or b"<MPD" in body[:2048]:
        return True, 98
    if content_type.startswith("video/"):
        return True, 96
    if content_type.startswith("audio/"):
        return True, 94
    if _looks_media_url(url):
        return True, 86
    return False, 0


def _yt_dlp_sources(page_url: str, validator: Callable[[str], object]) -> list[tuple[str, int]]:
    """Resolve a public player/page with yt-dlp when it has a supported extractor.

    This is an extractor fallback, not a protection bypass. DRM/auth/CAPTCHA
    failures remain failures and are never worked around here.
    """
    try:
        import yt_dlp
    except Exception:
        return []
    try:
        validator(page_url)
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": YTDLP_TIMEOUT,
            "retries": 1,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(page_url, download=False)
        if not isinstance(info, dict):
            return []

        results: list[tuple[str, int]] = []
        formats = info.get("formats") or []
        if formats:
            for fmt in formats:
                if not isinstance(fmt, dict):
                    continue
                media_url = fmt.get("url")
                if isinstance(media_url, str) and media_url.startswith(("http://", "https://")):
                    results.append((media_url, 118))
        direct = info.get("url")
        if isinstance(direct, str) and direct.startswith(("http://", "https://")):
            results.append((direct, 120))
        return results[:MAX_CANDIDATES]
    except Exception:
        return []


def resolve(
    url: str,
    *,
    validator: Callable[[str], object],
    request_factory: Callable[..., object],
    open_function: Callable[..., object],
    read_function: Callable[[object, int], bytes],
) -> list[str]:
    """Return ranked public media URLs discovered from a page/player chain."""
    try:
        validator(url)
    except Exception:
        return []

    queue: list[tuple[str, int]] = [(url, 0)]
    visited: set[str] = set()
    candidates: dict[str, MediaCandidate] = {}

    def add_candidate(candidate_url: str, score: int, kind: str, source_page: str, probe: bool = True) -> None:
        if len(candidates) >= MAX_CANDIDATES:
            return
        try:
            validator(candidate_url)
        except Exception:
            return
        if probe:
            ok, probe_score = _probe(
                candidate_url,
                validator=validator,
                request_factory=request_factory,
                open_function=open_function,
                referer=source_page,
            )
            if not ok:
                return
            score += probe_score
        current = candidates.get(candidate_url)
        item = MediaCandidate(candidate_url, score, kind, source_page)
        if current is None or item.score > current.score:
            candidates[candidate_url] = item

    while queue and len(visited) < MAX_PAGES:
        page_url, depth = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)

        # First ask yt-dlp about every page/player URL. A supported player
        # often exposes the final HLS/MP4 URL even when the HTML only contains
        # an opaque player configuration.
        for candidate_url, score in _yt_dlp_sources(page_url, validator):
            # yt-dlp has already classified these as media URLs. We still run
            # SSRF validation, but do not require a second HTTP probe because
            # signed/extensionless media URLs can reject anonymous probes.
            add_candidate(candidate_url, score, "extractor", page_url, probe=False)

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
            add_candidate(page_url, 120, "direct", url, probe=True)
            continue

        text = body.decode("utf-8", errors="ignore")
        media, pages = _extract(text, page_url)
        for candidate_url, score in media[:MAX_CANDIDATES]:
            add_candidate(candidate_url, score, "media", page_url, probe=True)

        if depth < MAX_EMBED_DEPTH:
            for candidate_url in pages[:MAX_PAGES]:
                try:
                    validator(candidate_url)
                except Exception:
                    continue
                if candidate_url not in visited and all(item[0] != candidate_url for item in queue):
                    queue.append((candidate_url, depth + 1))

    ranked = sorted(candidates.values(), key=lambda item: (-item.score, item.url))
    return [item.url for item in ranked[:8]]


def install(bot_module) -> None:
    """Install the resolver as a conservative fallback over the legacy extractor."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_smart_search_resolver", False):
        return

    def wrapped(url, *args, **kwargs):
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
                print(f"🔎 Smart Search Resolver: resolved {len(resolved)} public media candidate(s)", flush=True)
                return resolved
        except Exception as exc:
            print(f"⚠️ Smart Search Resolver skipped: {type(exc).__name__}", flush=True)
        return existing

    wrapped._smart_search_resolver = True
    bot_module.extract_direct_media_urls = wrapped
    print("🔎 Smart Search Resolver: ENABLED", flush=True)
