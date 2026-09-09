"""Dedicated public-link discovery for Shahid4u pages.

This adapter only follows public download/server links exposed by the page.
It does not bypass authentication, CAPTCHA, DRM, or other access controls.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlparse
from urllib.request import Request

HOST_SUFFIX = "shahid4u.run"
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_CANDIDATES = 16
TIMEOUT_SECONDS = 25
DOWNLOAD_TERMS = ("download", "تحميل", "تنزيل", "direct", "رابط التحميل")
QUALITY_RE = re.compile(r"(?:2160|1440|1080|720|480|360|240)\s*p", re.I)
MEDIA_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".flv", ".m3u8", ".mpd")
REJECT_HOSTS = ("microsoft.com", "google.com", "googleadservices.com", "doubleclick.net")


def _is_http(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _is_shahid4u(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host == HOST_SUFFIX or host.endswith("." + HOST_SUFFIX)


def _rejected_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return True
    return any(host == item or host.endswith("." + item) for item in REJECT_HOSTS)


def _probable_media(url: str, label: str = "") -> bool:
    if not _is_http(url) or _rejected_host(url):
        return False
    value = f"{label} {url}".casefold()
    if any(marker in value for marker in ("secure_stream", "direct_stream", "mycima")):
        return True
    if QUALITY_RE.search(value):
        return True
    if any(urlparse(url).path.lower().endswith(ext) for ext in MEDIA_EXTENSIONS):
        return True
    return any(term in value for term in DOWNLOAD_TERMS)


def _score(url: str, label: str = "") -> int:
    value = f"{label} {url}".casefold()
    score = 0
    if any(term in value for term in DOWNLOAD_TERMS):
        score += 100
    if "secure_stream" in value or "direct_stream" in value:
        score += 90
    if "mycima" in value:
        score += 70
    match = QUALITY_RE.search(value)
    if match:
        score += {"2160": 60, "1440": 55, "1080": 50, "720": 40, "480": 30, "360": 20, "240": 10}.get(match.group(0)[:-1], 0)
    if any(urlparse(url).path.lower().endswith(ext) for ext in MEDIA_EXTENSIONS):
        score += 40
    return score


def _extract(page_url: str, body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="ignore")
    ranked: dict[str, int] = {}
    for match in re.finditer(r"<a\\b[^>]*href\\s*=\\s*[\\\"']([^\\\"']+)[\\\"'][^>]*>(.*?)</a>", text, re.I | re.S):
        href = urljoin(page_url, html.unescape(match.group(1)))
        label = re.sub(r"<[^>]+>", " ", html.unescape(match.group(2)))
        label = re.sub(r"\\s+", " ", label).strip()
        if _probable_media(href, label):
            ranked[href] = max(ranked.get(href, 0), _score(href, label))
    for raw in re.findall(r"https?://[^\\\"'<>\\s]+", text, re.I):
        href = html.unescape(raw).rstrip(")>,;\\\"'")
        if _probable_media(href):
            ranked[href] = max(ranked.get(href, 0), _score(href))
    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, _ in ordered[:MAX_CANDIDATES]]


def resolve(url: str, *, validator, request_factory=Request, open_function=None, read_function=None) -> list[str]:
    """Return public download/server candidates exposed by a Shahid4u page."""
    if not _is_shahid4u(url) or open_function is None or read_function is None:
        return []
    try:
        validator(url)
        request = request_factory(url, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ar,en;q=0.8",
        })
        with open_function(request, timeout=TIMEOUT_SECONDS, max_bytes=MAX_HTML_BYTES) as response:
            body = read_function(response, MAX_HTML_BYTES)
        candidates = _extract(url, body)
    except Exception as exc:
        print(f"⚠️ Shahid4u Resolver: discovery failed ({type(exc).__name__})", flush=True)
        return []
    if candidates:
        print(f"🎯 Shahid4u Resolver: discovered {len(candidates)} public candidate(s)", flush=True)
    else:
        print("🎯 Shahid4u Resolver: no public download candidate found", flush=True)
    return candidates
