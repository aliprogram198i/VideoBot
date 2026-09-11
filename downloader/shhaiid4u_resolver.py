"""Dedicated bounded resolver for the independent shhaiid4u.net platform.

This layer only discovers public player/server/media candidates. It does not
bypass authentication, CAPTCHA, DRM, paywalls, or access controls.
"""
from __future__ import annotations

import asyncio
import inspect
from urllib.parse import urlparse, urlunparse

HOST_SUFFIX = "shhaiid4u.net"
MAX_CANDIDATES = 12
TIMEOUT_MS = 35_000
SETTLE_MS = 3_500
MAX_PAGES = 10
_AD_HOST_HINTS = (
    "doubleclick", "googlesyndication", "googleadservices", "adservice",
    "adsystem", "advertising", "adserver", "popads", "propellerads",
)
_MEDIA_HINTS = (
    ".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".ts",
    "secure_stream", "direct_stream", "download", "تحميل", "تنزيل",
)
_PLAYER_HINTS = (
    "player", "embed", "iframe", "server", "servers", "source", "stream",
    "سيرفر", "سيرفرات", "مشغل", "مشاهدة", "تشغيل", "تحميل", "تنزيل",
)


def _is_http(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def is_platform_url(url: str) -> bool:
    if not isinstance(url, str) or not _is_http(url):
        return False
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == HOST_SUFFIX or host.endswith("." + HOST_SUFFIX)


def _canonical_page_url(url: str) -> str:
    """Map the site's legacy /watch/<slug> route to its indexed /episode/<slug>."""
    if not is_platform_url(url):
        return url
    parsed = urlparse(url)
    path = parsed.path or ""
    if path == "/watch" or path.startswith("/watch/"):
        canonical_path = "/episode" + path[len("/watch"):]
        return urlunparse(parsed._replace(path=canonical_path))
    return url


def _is_ad_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return True
    return any(hint in host for hint in _AD_HOST_HINTS)


def _score(url: str) -> int:
    value = url.casefold()
    score = 0
    for marker in _MEDIA_HINTS:
        if marker.casefold() in value:
            score += 20
    for marker in _PLAYER_HINTS:
        if marker.casefold() in value:
            score += 5
    if _is_ad_host(url):
        score -= 500
    return score


def _normalize(candidates: list[str]) -> list[str]:
    unique: dict[str, int] = {}
    for candidate in candidates:
        if not isinstance(candidate, str) or not _is_http(candidate):
            continue
        if _is_ad_host(candidate):
            continue
        unique[candidate] = max(unique.get(candidate, -10**9), _score(candidate))
    ranked = sorted(unique.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, score in ranked if score > 0][:MAX_CANDIDATES]


def resolve(url: str, *, validator) -> list[str]:
    """Discover public media candidates from shhaiid4u.net via bounded browser discovery."""
    if not is_platform_url(url):
        return []
    canonical_url = _canonical_page_url(url)
    try:
        validator(canonical_url)
    except Exception:
        return []
    if canonical_url != url:
        print("🎯 Shhaiid4u Resolver: normalized /watch/ route to /episode/", flush=True)
    try:
        from downloader import browser_media_resolver
        candidates = browser_media_resolver.resolve(
            canonical_url,
            validator=validator,
            timeout_ms=TIMEOUT_MS,
            settle_ms=SETTLE_MS,
            max_candidates=MAX_CANDIDATES,
            max_pages=MAX_PAGES,
        )
    except Exception as exc:
        print(f"⚠️ Shhaiid4u Resolver: browser discovery failed ({type(exc).__name__})", flush=True)
        return []
    normalized = _normalize(candidates or [])
    if normalized:
        print(f"🎯 Shhaiid4u Resolver: discovered {len(normalized)} public candidate(s)", flush=True)
    else:
        print("🎯 Shhaiid4u Resolver: no public media candidate found", flush=True)
    return normalized


def install(bot_module) -> None:
    """Install an isolated extraction hook before the generic bridge is composed."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_shhaiid4u_resolver", False):
        return

    async def wrapped(url, *args, **kwargs):
        if is_platform_url(url):
            try:
                candidates = await asyncio.to_thread(
                    resolve,
                    url,
                    validator=bot_module.validate_public_http_url,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ Shhaiid4u Resolver: extraction hook failed ({type(exc).__name__})", flush=True)
                candidates = []
            if candidates:
                return candidates
            print("🎯 Shhaiid4u Resolver: falling through to generic extraction", flush=True)
        result = original(url, *args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    wrapped._shhaiid4u_resolver = True
    bot_module.extract_direct_media_urls = wrapped
    print("🎯 Shhaiid4u Resolver: ENABLED", flush=True)
