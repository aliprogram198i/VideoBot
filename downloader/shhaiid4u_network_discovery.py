"""Enhanced, bounded public-page discovery for shhaiid4u.net.

This module is deliberately isolated from the generic downloader. It discovers
public player/server/media candidates only; it does not bypass authentication,
CAPTCHA, DRM, paywalls, or access controls.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from urllib.parse import urljoin, urlparse

from downloader import shhaiid4u_resolver as base

MAX_NAV_TARGETS = 12
MAX_SERVER_CLICKS = 12
MAX_RESPONSES = 260
MAX_JSON_RESPONSES = 20
MAX_JSON_BYTES = 512 * 1024
MEDIA_TYPES = (
    "video/", "audio/", "application/vnd.apple.mpegurl", "application/x-mpegurl",
    "application/dash+xml",
)
MEDIA_EXTENSIONS = (".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".ts")
NAV_HINTS = (
    "player", "embed", "iframe", "server", "servers", "source", "stream",
    "download", "direct", "ajax", "api", "مشغل", "سيرفر", "سيرفرات",
    "مشاهدة", "تشغيل", "تحميل", "تنزيل",
)


def _score_navigation(text: str, href: str) -> int:
    value = f"{text} {href}".casefold()
    score = 0
    score += sum(12 for marker in ("server", "servers", "سيرفر", "سيرفرات") if marker.casefold() in value)
    score += sum(10 for marker in ("player", "embed", "iframe", "source", "stream", "مشغل", "مشاهدة") if marker.casefold() in value)
    score += sum(8 for marker in ("download", "direct", "تحميل", "تنزيل") if marker.casefold() in value)
    score += sum(5 for marker in ("ajax", "api") if marker.casefold() in value)
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        score += 15
    return score


def _looks_like_media(url: str, content_type: str = "") -> bool:
    if not base._is_http(url) or base._is_ad_host(url):
        return False
    path = urlparse(url).path.casefold()
    ctype = (content_type or "").casefold()
    return any(ctype.startswith(item) for item in MEDIA_TYPES) or any(path.endswith(ext) for ext in MEDIA_EXTENSIONS) or base._looks_like_candidate(url)


def _extract_urls(text: str, *, base_url: str) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    values = []
    values.extend(re.findall(r"https?://[^\\\"'<>\s]+", text))
    values.extend(item.replace("\\/", "/") for item in re.findall(r"https?:\\/\\/[^\"'<>\s]+", text))
    for match in re.findall(r"(?:^|[\"'`=:(,\s])((?:/|\./|\.\./)(?:[^\"'`<>\s]+))", text):
        values.append(urljoin(base_url, match.replace("\\/", "/")))
    result = []
    seen = set()
    for value in values:
        value = value.replace("\\/", "/").rstrip(".,);]}")
        if value in seen or not base._is_http(value) or base._is_ad_host(value):
            continue
        if base._looks_like_candidate(value) or _score_navigation("", value) >= 10:
            seen.add(value)
            result.append(value)
    return result


def _queue(queue: list[str], visited: set[str], url: str) -> None:
    if not base._is_http(url) or base._is_ad_host(url):
        return
    if url in visited or url in queue or len(queue) >= MAX_NAV_TARGETS:
        return
    queue.append(url)


async def _inspect(page, validator, candidates: dict[str, int], queue: list[str], visited: set[str]) -> None:
    try:
        rows = await page.locator("a[href], iframe[src], embed[src], video, source, [data-server], [data-player], [data-url], [data-download], [onclick]").evaluate_all(
            """els => els.map(el => ({
                href: el.href || el.src || el.currentSrc || el.getAttribute('src') || '',
                text: (el.innerText || el.textContent || el.value || '').trim(),
                attr: [el.className || '', el.id || '', el.getAttribute('onclick') || '',
                    el.getAttribute('data-server') || '', el.getAttribute('data-player') || '',
                    el.getAttribute('data-url') || '', el.getAttribute('data-download') || ''].join(' ').trim()
            }))"""
        )
    except Exception:
        rows = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or "")
        text = f"{row.get('text') or ''} {row.get('attr') or ''}"
        if href and base._is_http(href):
            try:
                validator(href)
            except Exception:
                continue
            if _looks_like_media(href):
                candidates[href] = max(candidates.get(href, 0), base._score(href) + 40)
            elif _score_navigation(text, href) > 0:
                _queue(queue, visited, href)
        for extracted in _extract_urls(text, base_url=page.url):
            try:
                validator(extracted)
            except Exception:
                continue
            if _looks_like_media(extracted):
                candidates[extracted] = max(candidates.get(extracted, 0), base._score(extracted) + 35)
            elif _score_navigation(text, extracted) > 0:
                _queue(queue, visited, extracted)
    try:
        html = await page.content()
    except Exception:
        html = ""
    for extracted in _extract_urls(html, base_url=page.url):
        try:
            validator(extracted)
        except Exception:
            continue
        if _looks_like_media(extracted):
            candidates[extracted] = max(candidates.get(extracted, 0), base._score(extracted) + 25)
        else:
            _queue(queue, visited, extracted)


async def _inspect_popup(popup, validator, candidates: dict[str, int]) -> None:
    await _inspect(popup, validator, candidates, [], set())
    try:
        await popup.wait_for_timeout(1200)
    except Exception:
        pass
    await _inspect(popup, validator, candidates, [], set())


async def _click_controls(page, validator, candidates: dict[str, int], queue: list[str], visited: set[str]) -> None:
    selector = "a[href], button, [role='button'], input[type='button'], input[type='submit'], [data-server], [data-player], [data-url], [data-download], [onclick]"
    try:
        rows = await page.locator(selector).evaluate_all(
            """els => els.map((el,index) => ({index, href: el.href || '',
                text: (el.innerText || el.textContent || el.value || '').trim(),
                attr: [el.className || '', el.id || '', el.getAttribute('onclick') || '',
                    el.getAttribute('data-server') || '', el.getAttribute('data-player') || '',
                    el.getAttribute('data-url') || '', el.getAttribute('data-download') || ''].join(' ').trim()}))"""
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
        score = _score_navigation(text, str(row.get("href") or ""))
        if score >= 10:
            ranked.append((score, index))
        for extracted in _extract_urls(text, base_url=page.url):
            if _looks_like_media(extracted):
                candidates[extracted] = max(candidates.get(extracted, 0), base._score(extracted) + 30)
            elif _score_navigation(text, extracted) >= 10:
                _queue(queue, visited, extracted)
    locator = page.locator(selector)
    for _, index in sorted(ranked, key=lambda item: (-item[0], item[1]))[:MAX_SERVER_CLICKS]:
        try:
            control = locator.nth(index)
            popup_holder = None
            try:
                async with page.expect_popup(timeout=1500) as popup_info:
                    await control.click(timeout=2500, no_wait_after=True)
                popup_holder = await popup_info.value
            except Exception:
                popup_holder = None
            if popup_holder is not None:
                try:
                    await popup_holder.wait_for_load_state("domcontentloaded", timeout=6000)
                    await _inspect_popup(popup_holder, validator, candidates)
                finally:
                    try:
                        await popup_holder.close()
                    except Exception:
                        pass
            else:
                await page.wait_for_timeout(900)
                await _inspect(page, validator, candidates, queue, visited)
        except Exception:
            continue


async def _discover(url: str, *, validator) -> list[str]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        print(f"⚠️ Shhaiid4u Network Discovery: Playwright unavailable ({type(exc).__name__})", flush=True)
        return []
    candidates: dict[str, int] = {}
    queue = [url]
    visited: set[str] = set()
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True, args=[
                "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                "--no-first-run", "--no-default-browser-check",
            ])
        except Exception as exc:
            print(f"⚠️ Shhaiid4u Network Discovery: Chromium launch failed ({type(exc).__name__})", flush=True)
            return []
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                java_script_enabled=True,
            )
            while queue and len(visited) < base.MAX_PAGES and len(candidates) < base.MAX_CANDIDATES:
                page_url = queue.pop(0)
                if page_url in visited:
                    continue
                visited.add(page_url)
                page = await context.new_page()
                response_count = 0
                json_count = 0

                async def on_response(response) -> None:
                    nonlocal response_count, json_count
                    if response_count >= MAX_RESPONSES:
                        return
                    response_count += 1
                    response_url = response.url
                    if not base._is_http(response_url) or base._is_ad_host(response_url):
                        return
                    try:
                        headers = response.headers
                        content_type = (headers.get("content-type") or "").lower()
                        content_length = int(headers.get("content-length") or "0")
                    except Exception:
                        content_type = ""
                        content_length = 0
                    if _looks_like_media(response_url, content_type):
                        try:
                            validator(response_url)
                        except Exception:
                            return
                        candidates[response_url] = max(candidates.get(response_url, 0), 150)
                        return
                    if _score_navigation("", response_url) >= 10:
                        _queue(queue, visited, response_url)
                    if json_count >= MAX_JSON_RESPONSES or "json" not in content_type or (content_length and content_length > MAX_JSON_BYTES):
                        return
                    json_count += 1
                    try:
                        body = await response.body()
                        if len(body) > MAX_JSON_BYTES:
                            return
                        text = body.decode("utf-8", "ignore")
                    except Exception:
                        return
                    for extracted in _extract_urls(text, base_url=response_url):
                        try:
                            validator(extracted)
                        except Exception:
                            continue
                        if _looks_like_media(extracted):
                            candidates[extracted] = max(candidates.get(extracted, 0), 140)
                        elif _score_navigation("", extracted) >= 10:
                            _queue(queue, visited, extracted)

                page.on("response", on_response)
                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=base.TIMEOUT_MS)
                    await page.wait_for_timeout(base.SETTLE_MS)
                    await _inspect(page, validator, candidates, queue, visited)
                    await _click_controls(page, validator, candidates, queue, visited)
                    await page.wait_for_timeout(base.SETTLE_MS)
                    await _inspect(page, validator, candidates, queue, visited)
                    for frame in page.frames:
                        frame_url = frame.url
                        if frame_url and frame_url != page_url:
                            _queue(queue, visited, frame_url)
                except Exception as exc:
                    print(f"⚠️ Shhaiid4u Network Discovery: page failed ({type(exc).__name__})", flush=True)
                finally:
                    await page.close()
            await context.close()
        finally:
            await browser.close()
    ranked = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
    print(f"🎯 Shhaiid4u Network Discovery: {'found ' + str(len(ranked)) + ' candidate(s)' if ranked else 'no public media candidate found'}", flush=True)
    return [url for url, _ in ranked[:base.MAX_CANDIDATES]]


def resolve(url: str, *, validator) -> list[str]:
    if not base.is_platform_url(url):
        return []
    canonical_url = base._canonical_page_url(url)
    try:
        validator(canonical_url)
    except Exception:
        return []
    if canonical_url != url:
        print("🎯 Shhaiid4u Network Discovery: normalized /watch/ route to /episode/", flush=True)
    try:
        return base._normalize(asyncio.run(_discover(canonical_url, validator=validator)))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return base._normalize(loop.run_until_complete(_discover(canonical_url, validator=validator)))
        finally:
            loop.close()
    except Exception as exc:
        print(f"⚠️ Shhaiid4u Network Discovery: failed ({type(exc).__name__})", flush=True)
        return []


def install(bot_module) -> None:
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_shhaiid4u_network_discovery", False):
        return

    async def wrapped(url, *args, **kwargs):
        if base.is_platform_url(url):
            try:
                candidates = await asyncio.to_thread(resolve, url, validator=bot_module.validate_public_http_url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ Shhaiid4u Network Discovery: hook failed ({type(exc).__name__})", flush=True)
                candidates = []
            if candidates:
                return candidates
            print("🎯 Shhaiid4u Network Discovery: falling through to existing resolver chain", flush=True)
        result = original(url, *args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    wrapped._shhaiid4u_network_discovery = True
    bot_module.extract_direct_media_urls = wrapped
    print("🎯 Shhaiid4u Network Discovery: ENABLED", flush=True)
