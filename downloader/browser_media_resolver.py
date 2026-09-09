"""Browser-backed discovery for JavaScript-driven public media players.

This module is a bounded last-resort discovery layer. It observes normal
browser navigation/network activity and follows public player/server links
exposed by the page. It does not solve CAPTCHAs, bypass authentication,
defeat DRM, or circumvent access controls.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from urllib.parse import urlparse


LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 25_000
DEFAULT_SETTLE_MS = 2_500
DEFAULT_MAX_CANDIDATES = 8
DEFAULT_MAX_RESPONSES = 220
DEFAULT_MAX_PAGES = 6
DEFAULT_MAX_NAV_TARGETS = 10
DEFAULT_MAX_SERVER_CLICKS = 8

_MEDIA_CONTENT_TYPES = (
    "video/",
    "audio/",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
)
_MEDIA_MARKERS = (
    ".m3u8",
    ".mpd",
    ".mp4",
    ".m4v",
    ".webm",
    ".mov",
)
_SERVER_WORDS = (
    "server", "servers", "watch", "player", "stream", "source",
    "سيرفر", "سيرفرات", "مشاهدة", "مشغل", "مشاهده", "تشغيل",
    "السيرفر", "السيرفرات",
)
_DOWNLOAD_WORDS = (
    "download", "downloads", "direct", "تحميل", "تحميل مباشر",
    "تنزيل", "رابط التحميل", "تحميل مباشر", "hd", "web-dl", "webrip",
)


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _is_media_response(url: str, content_type: str | None) -> bool:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if any(normalized.startswith(prefix) for prefix in _MEDIA_CONTENT_TYPES[:2]):
        return True
    if normalized in _MEDIA_CONTENT_TYPES[2:]:
        return True
    path = urlparse(url).path.lower()
    return any(marker in path for marker in _MEDIA_MARKERS)


def _score(url: str, content_type: str | None) -> int:
    normalized = (content_type or "").lower()
    path = urlparse(url).path.lower()
    if "mpegurl" in normalized or ".m3u8" in path:
        return 120
    if "dash+xml" in normalized or ".mpd" in path:
        return 115
    if normalized.startswith("video/"):
        return 110
    if normalized.startswith("audio/"):
        return 105
    return 100


def _browser_enabled() -> bool:
    value = os.getenv("ALIBOT_BROWSER_RESOLVER", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _navigation_score(text: str, href: str) -> int:
    value = f"{text} {href}".casefold()
    score = 0
    for word in _SERVER_WORDS:
        if word.casefold() in value:
            score += 10
    for word in _DOWNLOAD_WORDS:
        if word.casefold() in value:
            score += 12
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        score += 20
    if re.search(r"(?:720|1080|480|360)p", value):
        score += 12
    if any(token in value for token in ("embed", "iframe", "player")):
        score += 15
    return score


async def _discover_navigation_targets(page, base_url: str, max_targets: int) -> list[str]:
    """Collect public player/server/download URLs exposed after JavaScript executes."""
    try:
        rows = await page.locator("a[href], iframe[src], embed[src]").evaluate_all(
            """els => els.map(el => ({
                href: el.href || el.src || '',
                text: (el.innerText || el.textContent || '').trim(),
                attr: ((el.className || '') + ' ' + (el.id || '') + ' ' +
                       (el.getAttribute('data-server') || '') + ' ' +
                       (el.getAttribute('data-player') || '') + ' ' +
                       (el.getAttribute('data-download') || '')).trim()
            }))"""
        )
    except Exception:
        return []

    ranked: dict[str, tuple[int, str]] = {}
    base_host = (urlparse(base_url).hostname or "").lower()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        href = row.get("href")
        if not isinstance(href, str) or not _is_http_url(href) or href == base_url:
            continue
        text = " ".join(
            str(row.get(key) or "") for key in ("text", "attr")
        )
        score = _navigation_score(text, href)
        target_host = (urlparse(href).hostname or "").lower()
        if target_host and target_host != base_host:
            score += 8
        if score <= 0:
            continue
        current = ranked.get(href)
        if current is None or score > current[0]:
            ranked[href] = (score, text)

    ordered = sorted(ranked.items(), key=lambda item: (-item[1][0], item[0]))
    return [href for href, _ in ordered[:max_targets]]


async def _click_server_controls(page, max_clicks: int) -> None:
    """Trigger only strongly server/player-like controls, with strict bounds."""
    try:
        controls = await page.locator("button, [role='button'], input[type='button'], input[type='submit']").evaluate_all(
            """els => els.map((el, index) => ({
                index,
                text: (el.innerText || el.textContent || el.value || '').trim(),
                attr: ((el.className || '') + ' ' + (el.id || '') + ' ' +
                       (el.getAttribute('data-server') || '') + ' ' +
                       (el.getAttribute('data-player') || '')).trim()
            }))"""
        )
    except Exception:
        return

    ranked: list[tuple[int, int]] = []
    for row in controls or []:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        text = f"{row.get('text') or ''} {row.get('attr') or ''}"
        score = _navigation_score(text, "")
        if score >= 10:
            ranked.append((score, index))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    for _, index in ranked[:max_clicks]:
        try:
            control = page.locator("button, [role='button'], input[type='button'], input[type='submit']").nth(index)
            await control.click(timeout=1500, no_wait_after=True)
            await page.wait_for_timeout(350)
        except Exception:
            continue


async def _collect_dom_media(page, validator, candidates: dict[str, tuple[int, str | None]]) -> None:
    """Collect media URLs exposed as DOM properties after player initialization."""
    try:
        rows = await page.locator("video, audio, source").evaluate_all(
            """els => els.map(el => ({
                src: el.currentSrc || el.src || el.getAttribute('src') ||
                     el.getAttribute('data-src') || el.getAttribute('data-url') || '',
                type: el.getAttribute('type') || ''
            }))"""
        )
    except Exception:
        return

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        media_url = row.get("src")
        if not isinstance(media_url, str) or not _is_http_url(media_url):
            continue
        try:
            validator(media_url)
        except Exception:
            continue
        content_type = row.get("type") or None
        score = _score(media_url, content_type) + 12
        current = candidates.get(media_url)
        if current is None or score > current[0]:
            candidates[media_url] = (score, content_type)


async def _resolve_async(
    url: str,
    *,
    validator,
    timeout_ms: int,
    settle_ms: int,
    max_candidates: int,
    max_pages: int,
) -> list[str]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        LOG.warning("Browser resolver unavailable: %s", type(exc).__name__)
        print(f"⚠️ Browser resolver unavailable: {type(exc).__name__}", flush=True)
        return []

    try:
        validator(url)
    except Exception:
        return []

    candidates: dict[str, tuple[int, str | None]] = {}
    visited_pages: set[str] = set()
    queue: list[str] = [url]

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
        except Exception as exc:
            LOG.warning("Browser resolver Chromium launch failed: %s", type(exc).__name__)
            print(f"⚠️ Browser resolver Chromium launch failed: {type(exc).__name__}", flush=True)
            return []
        try:
            print("🌐 Browser Media Resolver: Chromium started", flush=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 10; K) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0.0.0 Mobile Safari/537.36"
                ),
                java_script_enabled=True,
                ignore_https_errors=False,
            )

            while queue and len(visited_pages) < max_pages:
                page_url = queue.pop(0)
                if page_url in visited_pages:
                    continue
                visited_pages.add(page_url)
                page = await context.new_page()
                responses_seen = 0

                async def on_response(response) -> None:
                    nonlocal responses_seen
                    if responses_seen >= DEFAULT_MAX_RESPONSES:
                        return
                    responses_seen += 1
                    response_url = response.url
                    if not _is_http_url(response_url):
                        return
                    try:
                        content_type = response.headers.get("content-type")
                    except Exception:
                        content_type = None
                    if not _is_media_response(response_url, content_type):
                        return
                    try:
                        validator(response_url)
                    except Exception:
                        return
                    score = _score(response_url, content_type)
                    current = candidates.get(response_url)
                    if current is None or score > current[0]:
                        candidates[response_url] = (score, content_type)

                page.on("response", on_response)
                try:
                    await page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    await page.wait_for_timeout(settle_ms)
                    await _collect_dom_media(page, validator, candidates)

                    targets = await _discover_navigation_targets(
                        page, page_url, DEFAULT_MAX_NAV_TARGETS
                    )
                    for target in targets:
                        if target not in visited_pages and target not in queue:
                            queue.append(target)

                    await _click_server_controls(page, DEFAULT_MAX_SERVER_CLICKS)
                    await page.wait_for_timeout(settle_ms)
                    await _collect_dom_media(page, validator, candidates)

                    frame_urls = []
                    for frame in page.frames:
                        frame_url = frame.url
                        if (
                            frame_url
                            and frame_url != page_url
                            and _is_http_url(frame_url)
                            and frame_url not in visited_pages
                            and frame_url not in frame_urls
                        ):
                            frame_urls.append(frame_url)
                    queue.extend(frame_urls[:DEFAULT_MAX_NAV_TARGETS])
                except Exception as exc:
                    LOG.debug("Browser page resolution failed: %s", type(exc).__name__)
                    print(f"⚠️ Browser page resolution failed: {type(exc).__name__}", flush=True)
                finally:
                    await page.close()

                if len(candidates) >= max_candidates:
                    break

            await context.close()
        finally:
            await browser.close()

    ranked = sorted(candidates.items(), key=lambda item: (-item[1][0], item[0]))
    return [media_url for media_url, _ in ranked[:max_candidates]]


def resolve(
    url: str,
    *,
    validator,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    settle_ms: int = DEFAULT_SETTLE_MS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[str]:
    """Resolve public media URLs exposed by a JS-driven player/server chain."""
    if not _browser_enabled():
        return []
    if not isinstance(url, str) or not url.strip():
        return []
    if timeout_ms <= 0 or settle_ms < 0 or max_candidates <= 0 or max_pages <= 0:
        return []

    try:
        return asyncio.run(
            asyncio.wait_for(
                _resolve_async(
                    url.strip(),
                    validator=validator,
                    timeout_ms=timeout_ms,
                    settle_ms=settle_ms,
                    max_candidates=max_candidates,
                    max_pages=max_pages,
                ),
                timeout=(timeout_ms / 1000.0 + settle_ms / 1000.0 + 10.0) * max_pages,
            )
        )
    except Exception as exc:
        LOG.warning("Browser media resolver failed: %s", type(exc).__name__)
        print(f"⚠️ Browser media resolver failed: {type(exc).__name__}", flush=True)
        return []
