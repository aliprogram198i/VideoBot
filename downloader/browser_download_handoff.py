"""Bounded handoff for browser-triggered public media downloads.

This layer is intentionally separate from media discovery. It consumes a
public candidate already discovered by the browser resolver, preserves the
source-page browser context, triggers the normal browser download action, and
streams a verified media response into the caller's temporary directory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 25_000
DEFAULT_SETTLE_MS = 2_000
DEFAULT_MAX_CLICKS = 8
DEFAULT_MAX_FILE_BYTES = 500 * 1024 * 1024

_DOWNLOAD_WORDS = (
    "download", "downloads", "direct", "تحميل", "تحميل مباشر",
    "تنزيل", "رابط التحميل", "hd", "web-dl", "webrip",
)
_SERVER_WORDS = (
    "server", "servers", "watch", "player", "stream", "source",
    "سيرفر", "سيرفرات", "مشاهدة", "مشغل", "تشغيل", "السيرفر",
    "السيرفرات",
)
_MEDIA_TYPES = (
    "video/", "audio/", "application/octet-stream", "application/mp4",
    "application/x-mpegurl", "application/vnd.apple.mpegurl",
)
_PLAYLIST_TYPES = ("mpegurl", "m3u8", "dash", "mpd")


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _score_control(text: str, href: str) -> int:
    value = f"{text} {href}".casefold()
    score = sum(16 for word in _DOWNLOAD_WORDS if word.casefold() in value)
    score += sum(7 for word in _SERVER_WORDS if word.casefold() in value)
    if any(token in value for token in ("embed", "iframe", "player")):
        score += 10
    if any(token in value for token in ("1080p", "720p", "480p", "360p")):
        score += 12
    return score


async def _click_controls(page, max_clicks: int) -> None:
    selector = "a[href], button, [role='button'], input[type='button'], input[type='submit']"
    try:
        rows = await page.locator(selector).evaluate_all(
            """els => els.map((el,index) => ({
                index,
                href: el.href || '',
                text: (el.innerText || el.textContent || el.value || '').trim(),
                attr: ((el.className || '') + ' ' + (el.id || '') + ' ' +
                       (el.getAttribute('data-server') || '') + ' ' +
                       (el.getAttribute('data-player') || '') + ' ' +
                       (el.getAttribute('data-download') || '')).trim()
            }))"""
        )
    except Exception:
        return

    ranked = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        text = f"{row.get('text') or ''} {row.get('attr') or ''}"
        href = str(row.get("href") or "")
        score = _score_control(text, href)
        if score >= 14:
            ranked.append((score, index))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    locator = page.locator(selector)

    for _, index in ranked[:max_clicks]:
        try:
            await locator.nth(index).click(timeout=1800, no_wait_after=True)
            await page.wait_for_timeout(500)
        except Exception:
            continue


async def _candidate_links(page, base_url: str, max_links: int = 8) -> list[str]:
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

    base_host = (urlparse(base_url).hostname or "").lower()
    ranked = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        href = row.get("href")
        if not isinstance(href, str) or not _is_http_url(href) or href == base_url:
            continue
        text = f"{row.get('text') or ''} {row.get('attr') or ''}"
        score = _score_control(text, href)
        host = (urlparse(href).hostname or "").lower()
        if host and host != base_host:
            score += 8
        if score <= 0:
            continue
        ranked[href] = max(score, ranked.get(href, 0))

    return [href for href, _ in sorted(ranked.items(), key=lambda item: (-item[1], item[0]))[:max_links]]


def _safe_filename(name: str, is_audio: bool) -> str:
    suffix = Path(name or "").suffix.lower()
    allowed = {".mp3", ".m4a", ".opus", ".aac", ".wav"} if is_audio else {".mp4", ".mkv", ".webm", ".mov", ".ts"}
    if suffix not in allowed:
        suffix = ".mp3" if is_audio else ".mp4"
    stem = Path(name or "browser_download").stem or "browser_download"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem)
    return (safe[:80] or "browser_download") + suffix


def _looks_like_media(content_type: str, candidate_url: str, is_audio: bool) -> bool:
    value = (content_type or "").casefold()
    path = urlparse(candidate_url).path.casefold()
    if any(token in value or token in path for token in _PLAYLIST_TYPES):
        return False
    if is_audio:
        return value.startswith("audio/") or "octet-stream" in value or path.endswith((".mp3", ".m4a", ".aac", ".opus", ".wav"))
    return value.startswith("video/") or "octet-stream" in value or path.endswith((".mp4", ".mkv", ".webm", ".mov", ".ts"))


def _cookie_header(cookies: list[dict]) -> str:
    pairs = []
    for cookie in cookies or []:
        name = cookie.get("name")
        value = cookie.get("value")
        if isinstance(name, str) and isinstance(value, str):
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _stream_with_browser_context(candidate_url: str, output_dir: str, *, cookies: list[dict], referer_url: str | None, is_audio: bool, max_file_bytes: int, timeout_s: float) -> str | None:
    """Stream a public candidate using cookies established by Chromium.

    This is deliberately a streaming fallback: it never loads the media body
    into memory, so large-file handling remains compatible with the existing
    splitter/Telegram pipeline.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
        "Accept": "video/*,audio/*,application/octet-stream;q=0.9,*/*;q=0.2",
    }
    cookie_value = _cookie_header(cookies)
    if cookie_value:
        headers["Cookie"] = cookie_value
    if referer_url:
        headers["Referer"] = referer_url

    fd, target = tempfile.mkstemp(prefix="browser_stream_", suffix=_safe_filename("media", is_audio)[-5:], dir=output_dir)
    os.close(fd)
    total = 0
    try:
        request = Request(candidate_url, headers=headers, method="GET")
        with urlopen(request, timeout=timeout_s) as response:
            content_type = response.headers.get("Content-Type", "")
            content_length = response.headers.get("Content-Length")
            try:
                declared = int(content_length) if content_length else 0
            except ValueError:
                declared = 0
            if declared > max_file_bytes or not _looks_like_media(content_type, candidate_url, is_audio):
                return None

            suggested = response.headers.get_filename() or ""
            suffix = Path(_safe_filename(suggested or "media", is_audio)).suffix
            final_target = target
            if suffix:
                renamed = target + suffix
                os.replace(target, renamed)
                final_target = renamed

            with open(final_target, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_file_bytes:
                        return None
                    output.write(chunk)
            if total <= 0:
                return None
            LOG.info("Browser context stream saved %d bytes", total)
            print(f"🌐 Browser Download Handoff: streamed {total} bytes", flush=True)
            return final_target
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    finally:
        if not locals().get("final_target") or not os.path.exists(locals().get("final_target", "")):
            try:
                os.remove(target)
            except OSError:
                pass


async def _save_async(candidate_url: str, output_dir: str, *, validator, is_audio: bool, timeout_ms: int, settle_ms: int, max_file_bytes: int, referer_url: str | None = None) -> str | None:
    if not _is_http_url(candidate_url):
        return None
    try:
        validator(candidate_url)
        if referer_url:
            validator(referer_url)
    except Exception:
        return None

    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None

    os.makedirs(output_dir, exist_ok=True)

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                    "--no-first-run", "--no-default-browser-check",
                ],
            )
        except Exception:
            return None

        try:
            context_kwargs = {
                "user_agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                "java_script_enabled": True,
                "accept_downloads": True,
            }
            if referer_url:
                context_kwargs["extra_http_headers"] = {"Referer": referer_url}
            context = await browser.new_context(**context_kwargs)

            # Establish the same first-party/browser context before touching the
            # external candidate. This preserves cookies/session state used by
            # the source page and fixes candidates that reject isolated requests.
            if referer_url:
                source_page = await context.new_page()
                try:
                    await source_page.goto(referer_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    await source_page.wait_for_timeout(settle_ms)
                except Exception:
                    pass
                finally:
                    await source_page.close()

            cookies = await context.cookies()
            queue = [candidate_url]
            visited = set()

            while queue and len(visited) < 4:
                page_url = queue.pop(0)
                if page_url in visited:
                    continue
                visited.add(page_url)
                page = await context.new_page()
                try:
                    async with page.expect_download(timeout=timeout_ms) as download_info:
                        await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms, referer=referer_url)
                        await page.wait_for_timeout(settle_ms)
                        await _click_controls(page, DEFAULT_MAX_CLICKS)
                    download = await download_info.value
                    suggested = download.suggested_filename or ""
                    target_suffix = Path(_safe_filename(suggested, is_audio)).suffix
                    fd, target = tempfile.mkstemp(prefix="browser_", suffix=target_suffix, dir=output_dir)
                    os.close(fd)
                    await download.save_as(target)
                    size = os.path.getsize(target)
                    if 0 < size <= max_file_bytes:
                        LOG.info("Browser download handoff saved %d bytes", size)
                        print(f"🌐 Browser Download Handoff: saved {size} bytes", flush=True)
                        return target
                    try:
                        os.remove(target)
                    except OSError:
                        pass
                except Exception:
                    pass

                # The browser may receive an inline media response rather than
                # emitting a Playwright Download event. Stream that candidate
                # with the cookies captured from the now-primed browser context.
                stream_result = await asyncio.to_thread(
                    _stream_with_browser_context,
                    page_url,
                    output_dir,
                    cookies=cookies,
                    referer_url=referer_url,
                    is_audio=is_audio,
                    max_file_bytes=max_file_bytes,
                    timeout_s=max(5.0, timeout_ms / 1000.0),
                )
                if stream_result:
                    return stream_result

                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms, referer=referer_url)
                    await page.wait_for_timeout(settle_ms)
                    await _click_controls(page, DEFAULT_MAX_CLICKS)
                    for link in await _candidate_links(page, page_url):
                        if link not in visited and link not in queue:
                            queue.append(link)
                except Exception:
                    pass
                finally:
                    await page.close()

            await context.close()
        finally:
            await browser.close()

    return None


def resolve_to_file(candidate_url: str, output_dir: str, *, validator, is_audio: bool = False, timeout_ms: int = DEFAULT_TIMEOUT_MS, settle_ms: int = DEFAULT_SETTLE_MS, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES, referer_url: str | None = None) -> str | None:
    """Trigger a public browser download and return a verified local file."""
    try:
        return asyncio.run(_save_async(candidate_url, output_dir, validator=validator, is_audio=is_audio, timeout_ms=timeout_ms, settle_ms=settle_ms, max_file_bytes=max_file_bytes, referer_url=referer_url))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_save_async(candidate_url, output_dir, validator=validator, is_audio=is_audio, timeout_ms=timeout_ms, settle_ms=settle_ms, max_file_bytes=max_file_bytes, referer_url=referer_url))
        finally:
            loop.close()
    except Exception as exc:
        LOG.warning("Browser download handoff failed: %s", type(exc).__name__)
        return None
