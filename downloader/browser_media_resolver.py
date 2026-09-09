"""Optional browser/network media discovery for JavaScript-driven players.

This module is a last-resort discovery layer. It uses Playwright only when the
static extractor cannot resolve a public media URL. It observes normal browser
network traffic and returns public media resources exposed by the page/player.
It does not solve CAPTCHAs, bypass authentication, defeat DRM, or decrypt
protected streams.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse


LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 25_000
DEFAULT_SETTLE_MS = 2_500
DEFAULT_MAX_CANDIDATES = 8
DEFAULT_MAX_RESPONSES = 160
DEFAULT_MAX_PAGES = 3

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
        return []

    try:
        validator(url)
    except Exception:
        return []

    candidates: dict[str, tuple[int, str | None]] = {}
    visited_pages: set[str] = set()
    queue: list[str] = [url]

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 10; K) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0.0.0 Mobile Safari/537.36"
                ),
                java_script_enabled=True,
                ignore_https_errors=False,
            )

            for page_url in queue:
                if len(visited_pages) >= max_pages or page_url in visited_pages:
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
                        response_headers = response.headers
                        content_type = response_headers.get("content-type")
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
                    # Give player initialization and delayed source requests a
                    # bounded opportunity to run. We do not click controls or
                    # interact with authentication/anti-bot challenges.
                    await page.wait_for_timeout(settle_ms)

                    # Collect same-origin iframe URLs. The browser may expose
                    # their media requests independently, so inspect only a
                    # small bounded number of frames/pages.
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
                    queue.extend(frame_urls[: max(0, max_pages - len(visited_pages))])
                except Exception as exc:
                    LOG.debug("Browser page resolution failed: %s", type(exc).__name__)
                finally:
                    await page.close()

                if len(candidates) >= max_candidates:
                    break

            await context.close()
        finally:
            await browser.close()

    ranked = sorted(candidates.items(), key=lambda item: (-item[1][0], item[0]))
    return [url for url, _ in ranked[:max_candidates]]


def resolve(
    url: str,
    *,
    validator,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    settle_ms: int = DEFAULT_SETTLE_MS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[str]:
    """Resolve public media URLs exposed by a JavaScript-driven page."""
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
        return []
