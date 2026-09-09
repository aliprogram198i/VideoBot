"""Bounded handoff for browser-triggered public media downloads.

This layer is intentionally separate from media discovery. It consumes a
public candidate already discovered by the browser resolver, triggers the
normal browser download action, saves the resulting file into the caller's
temporary directory, and returns a verified local path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

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
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
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
                    if size <= 0 or size > max_file_bytes:
                        try:
                            os.remove(target)
                        except OSError:
                            pass
                        continue
                    LOG.info("Browser download handoff saved %d bytes", size)
                    print(f"🌐 Browser Download Handoff: saved {size} bytes", flush=True)
                    return target
                except Exception:
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
