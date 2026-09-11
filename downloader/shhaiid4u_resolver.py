"""Dedicated bounded resolver for the independent shhaiid4u.net platform.

This layer only discovers public player/server/media candidates. It does not
bypass authentication, CAPTCHA, DRM, paywalls, or access controls.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from urllib.parse import urlparse, urlunparse

HOST_SUFFIX = "shhaiid4u.net"
MAX_CANDIDATES = 12
TIMEOUT_MS = 35_000
SETTLE_MS = 3_500
MAX_PAGES = 10
MAX_RESPONSES = 220
MAX_NAV_TARGETS = 10
MAX_SERVER_CLICKS = 8
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
_MEDIA_EXTENSIONS = (".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".ts")


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
        return urlunparse(parsed._replace(path="/episode" + path[len("/watch"):]))
    return url


def _is_ad_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return True
    return any(hint in host for hint in _AD_HOST_HINTS)


def _score(url: str) -> int:
    value = url.casefold()
    score = sum(20 for marker in _MEDIA_HINTS if marker.casefold() in value)
    score += sum(5 for marker in _PLAYER_HINTS if marker.casefold() in value)
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


def _looks_like_candidate(url: str) -> bool:
    if not _is_http(url) or _is_ad_host(url):
        return False
    value = url.casefold()
    return any(marker.casefold() in value for marker in _MEDIA_HINTS + _PLAYER_HINTS)


def _extract_urls_from_text(text: str) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    # Handles normal URLs and common JSON/JS escaped URLs without executing JS.
    raw = re.findall(r"https?://[^\\\"'<>\s]+", text)
    # Escaped JSON/JS URLs may contain \\/ throughout the URL, so the body
    # matcher must allow backslashes and normalization happens afterwards.
    escaped = re.findall(r"https?:\\/\\/[^\"'<>\s]+", text)
    values = raw + [item.replace("\\/", "/") for item in escaped]
    return [item.rstrip(".,);]}") for item in values if _looks_like_candidate(item)]


def _navigation_score(text: str, href: str) -> int:
    value = f"{text} {href}".casefold()
    score = sum(16 for word in ("download", "تحميل", "تنزيل", "direct") if word.casefold() in value)
    score += sum(7 for word in _PLAYER_HINTS if word.casefold() in value)
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        score += 12
    if any(token in value for token in ("embed", "iframe", "player")):
        score += 10
    return score


async def _inspect_page(page, validator, candidates: dict[str, int], queue: list[str], visited: set[str]) -> None:
    """Inspect public DOM/HTML only; never execute extracted strings as code."""
    try:
        rows = await page.locator("a[href], iframe[src], embed[src], video, source").evaluate_all(
            """els => els.map(el => ({href: el.href || el.src || el.currentSrc || el.getAttribute('src') || '',
            text: (el.innerText || el.textContent || '').trim(),
            attr: ((el.className || '') + ' ' + (el.id || '') + ' ' +
            (el.getAttribute('onclick') || '') + ' ' + (el.getAttribute('data-server') || '') + ' ' +
            (el.getAttribute('data-player') || '') + ' ' + (el.getAttribute('data-url') || '')).trim()}))"""
        )
    except Exception:
        rows = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        href = row.get("href")
        text = " ".join(str(row.get(key) or "") for key in ("text", "attr"))
        if isinstance(href, str) and _is_http(href):
            try:
                validator(href)
            except Exception:
                continue
            if _looks_like_candidate(href):
                candidates[href] = max(candidates.get(href, 0), _score(href) + 20)
            elif _navigation_score(text, href) > 0 and href not in visited and href not in queue:
                queue.append(href)
        for extracted in _extract_urls_from_text(text):
            try:
                validator(extracted)
            except Exception:
                continue
            if _looks_like_candidate(extracted):
                candidates[extracted] = max(candidates.get(extracted, 0), _score(extracted) + 15)
            elif extracted not in visited and extracted not in queue:
                queue.append(extracted)
    try:
        html = await page.content()
    except Exception:
        html = ""
    for extracted in _extract_urls_from_text(html):
        try:
            validator(extracted)
        except Exception:
            continue
        if _looks_like_candidate(extracted):
            candidates[extracted] = max(candidates.get(extracted, 0), _score(extracted) + 10)


async def _click_server_controls(page, max_clicks: int, validator, candidates: dict[str, int]) -> None:
    selector = "a[href], button, [role='button'], input[type='button'], input[type='submit']"
    try:
        rows = await page.locator(selector).evaluate_all(
            """els => els.map((el,index) => ({index, href: el.href || '',
            text: (el.innerText || el.textContent || el.value || '').trim(),
            attr: ((el.className || '') + ' ' + (el.id || '') + ' ' +
            (el.getAttribute('onclick') || '') + ' ' + (el.getAttribute('data-server') || '') + ' ' +
            (el.getAttribute('data-player') || '') + ' ' + (el.getAttribute('data-download') || '')).trim()}))"""
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
        score = _navigation_score(text, str(row.get("href") or ""))
        if score >= 14:
            ranked.append((score, index))
    locator = page.locator(selector)
    for _, index in sorted(ranked, key=lambda item: (-item[0], item[1]))[:max_clicks]:
        try:
            control = locator.nth(index)
            async with page.expect_popup(timeout=1800) as popup_info:
                await control.click(timeout=1800, no_wait_after=True)
            popup = await popup_info.value
            await popup.wait_for_load_state("domcontentloaded", timeout=5000)
            await _inspect_page(popup, validator, candidates, [], set())
            await popup.wait_for_timeout(1200)
            await _inspect_page(popup, validator, candidates, [], set())
            await popup.close()
        except Exception:
            try:
                await locator.nth(index).click(timeout=1800, no_wait_after=True)
                await page.wait_for_timeout(700)
                await _inspect_page(page, validator, candidates, [], set())
            except Exception:
                continue


async def _browser_discover(url: str, *, validator) -> list[str]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        print(f"⚠️ Shhaiid4u Resolver: Playwright unavailable ({type(exc).__name__})", flush=True)
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
            print(f"⚠️ Shhaiid4u Resolver: Chromium launch failed ({type(exc).__name__})", flush=True)
            return []
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                java_script_enabled=True,
            )
            while queue and len(visited) < MAX_PAGES and len(candidates) < MAX_CANDIDATES:
                page_url = queue.pop(0)
                if page_url in visited or not _is_http(page_url):
                    continue
                visited.add(page_url)
                page = await context.new_page()
                response_count = 0

                async def on_response(response) -> None:
                    nonlocal response_count
                    if response_count >= MAX_RESPONSES:
                        return
                    response_count += 1
                    response_url = response.url
                    if not _is_http(response_url) or _is_ad_host(response_url):
                        return
                    try:
                        content_type = (response.headers.get("content-type") or "").lower()
                    except Exception:
                        content_type = ""
                    path = urlparse(response_url).path.lower()
                    if (content_type.startswith("video/") or content_type.startswith("audio/") or
                        "mpegurl" in content_type or "dash+xml" in content_type or
                        any(ext in path for ext in _MEDIA_EXTENSIONS)):
                        try:
                            validator(response_url)
                        except Exception:
                            return
                        candidates[response_url] = max(candidates.get(response_url, 0), 130)

                page.on("response", on_response)
                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    await page.wait_for_timeout(SETTLE_MS)
                    await _inspect_page(page, validator, candidates, queue, visited)
                    await _click_server_controls(page, MAX_SERVER_CLICKS, validator, candidates)
                    await page.wait_for_timeout(SETTLE_MS)
                    await _inspect_page(page, validator, candidates, queue, visited)
                    for frame in page.frames:
                        frame_url = frame.url
                        if frame_url and frame_url != page_url and _is_http(frame_url) and frame_url not in visited and frame_url not in queue:
                            queue.append(frame_url)
                except Exception as exc:
                    print(f"⚠️ Shhaiid4u Resolver: page discovery failed ({type(exc).__name__})", flush=True)
                finally:
                    await page.close()
            await context.close()
        finally:
            await browser.close()
    ranked = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
    print(f"🎯 Shhaiid4u Resolver: {'discovered ' + str(len(ranked)) + ' public candidate(s)' if ranked else 'no public media candidate found'}", flush=True)
    return [url for url, _ in ranked[:MAX_CANDIDATES]]


def resolve(url: str, *, validator) -> list[str]:
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
        return _normalize(asyncio.run(_browser_discover(canonical_url, validator=validator)))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return _normalize(loop.run_until_complete(_browser_discover(canonical_url, validator=validator)))
        finally:
            loop.close()
    except Exception as exc:
        print(f"⚠️ Shhaiid4u Resolver: browser discovery failed ({type(exc).__name__})", flush=True)
        return []


def install(bot_module) -> None:
    """Install an isolated extraction hook before the generic bridge is composed."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_shhaiid4u_resolver", False):
        return

    async def wrapped(url, *args, **kwargs):
        if is_platform_url(url):
            try:
                candidates = await asyncio.to_thread(resolve, url, validator=bot_module.validate_public_http_url)
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
