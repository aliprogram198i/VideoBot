"""Dedicated, bounded resolver for KRX18 public movie pages.

This adapter follows only public server/player links exposed by the requested
movie page. It does not bypass authentication, CAPTCHA, DRM, paywalls, or
other access controls.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
from urllib.parse import urlparse

KRX18_HOST = "krx18.com"
HARD_BUDGET_SECONDS = 30.0
SERVER_LIMIT = 3
SERVER_PAGE_TIMEOUT_MS = 7_000
SERVER_SETTLE_MS = 700
MAX_MEDIA_CANDIDATES = 12
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MIN_VIDEO_BYTES = 5 * 1024 * 1024
MIN_VIDEO_DURATION = 60.0

_MEDIA_MARKERS = (".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov", ".mkv")
_SERVER_WORDS = ("server", "servers", "watch", "player", "stream", "source", "سيرفر", "مشاهدة", "مشغل", "تشغيل")
_DOWNLOAD_WORDS = ("download", "تحميل", "تنزيل", "direct", "رابط التحميل")


def is_krx18_url(value: str) -> bool:
    try:
        host = (urlparse(str(value)).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host == KRX18_HOST or host.endswith("." + KRX18_HOST)


def _slug_tokens(url: str) -> set[str]:
    try:
        slug = urlparse(url).path.rsplit("/", 1)[-1].lower()
    except Exception:
        return set()
    slug = re.sub(r"[^a-z0-9]+", " ", slug)
    return {t for t in slug.split() if len(t) >= 4 and not t.isdigit()}


def _identity_match(source_url: str, title: str, metadata: str, body_text: str) -> bool:
    tokens = _slug_tokens(source_url)
    if not tokens:
        return True
    haystack = f"{title} {metadata} {body_text}".lower()
    numeric_id = re.search(r"/movies/(\d+)-", source_url.lower())
    if numeric_id and numeric_id.group(1) in haystack:
        return True
    hits = sum(1 for token in tokens if token in haystack)
    return hits >= min(3, max(2, len(tokens) // 4))


def _score_link(text: str, href: str) -> int:
    value = f"{text} {href}".lower()
    score = 0
    score += sum(10 for word in _SERVER_WORDS if word in value)
    score += sum(8 for word in _DOWNLOAD_WORDS if word in value)
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        score += 12
    if any(marker in value for marker in ("embed", "iframe", "player")):
        score += 5
    return score


def _is_media_response(url: str, content_type: str | None, disposition: str | None) -> bool:
    value = (content_type or "").lower()
    if value.startswith("video/") or value.startswith("audio/"):
        return True
    if "mpegurl" in value or "dash+xml" in value:
        return True
    if "attachment" in (disposition or "").lower():
        return True
    path = urlparse(url).path.lower()
    return any(marker in path for marker in _MEDIA_MARKERS)


async def _extract_server_links(page, source_url: str, limit: int) -> list[str]:
    selectors = (
        "a[href], iframe[src], embed[src], "
        "[data-server], [data-player], [data-download], [data-url], [data-href]"
    )
    try:
        rows = await page.locator(selectors).evaluate_all(
            """els => els.map((el,index) => ({
                index,
                href: el.href || el.src || el.getAttribute('data-url') || el.getAttribute('data-href') || '',
                text: (el.innerText || el.textContent || el.value || '').trim(),
                attr: [el.getAttribute('data-server'), el.getAttribute('data-player'), el.getAttribute('data-download'), el.className, el.id]
                    .filter(Boolean).join(' '),
                onclick: el.getAttribute('onclick') || ''
            }))"""
        )
    except Exception:
        return []

    ranked: dict[str, int] = {}
    base_host = (urlparse(source_url).hostname or "").lower()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw = " ".join(str(row.get(k) or "") for k in ("href", "onclick"))
        urls = re.findall(r"https?://[^\"'\s<>]+", raw)
        if row.get("href"):
            urls.append(str(row["href"]))
        text = f"{row.get('text') or ''} {row.get('attr') or ''}"
        score = _score_link(text, raw)
        for candidate in urls:
            candidate = candidate.rstrip("'\".,);}")
            try:
                parsed = urlparse(candidate)
            except Exception:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            if candidate == source_url:
                continue
            if (parsed.hostname or "").lower() == base_host and score < 12:
                continue
            if score <= 0:
                continue
            ranked[candidate] = max(ranked.get(candidate, 0), score)

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, _ in ordered[:limit]]


async def _collect_server_media(page, validator, deadline: float) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def remember(value: str) -> None:
        if value not in seen and len(candidates) < MAX_MEDIA_CANDIDATES:
            seen.add(value)
            candidates.append(value)

    async def on_response(response) -> None:
        if time.monotonic() >= deadline:
            return
        try:
            url = response.url
            headers = response.headers
            if not _is_media_response(url, headers.get("content-type"), headers.get("content-disposition")):
                return
            validator(url)
            remember(url)
        except Exception:
            return

    async def on_download(download) -> None:
        if time.monotonic() >= deadline:
            return
        try:
            url = download.url
            validator(url)
            remember(url)
        except Exception:
            return

    page.on("response", on_response)
    page.on("download", on_download)
    try:
        await page.wait_for_timeout(SERVER_SETTLE_MS)
        try:
            rows = await page.locator("video, audio, source").evaluate_all(
                """els => els.map(el => el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || '')"""
            )
        except Exception:
            rows = []
        for value in rows or []:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                try:
                    validator(value)
                    remember(value)
                except Exception:
                    pass
        try:
            await page.locator("a[href], button, [role='button']").evaluate_all(
                """els => els.filter(el => ((el.innerText || el.textContent || '') + ' ' + (el.className || '')).match(/server|player|watch|download|سيرفر|مشاهدة|تحميل/i)).slice(0,6).forEach(el => el.click())"""
            )
        except Exception:
            pass
        await page.wait_for_timeout(SERVER_SETTLE_MS)
    finally:
        pass
    return candidates


async def _resolve_async(source_url: str, validator) -> list[tuple[str, str]]:
    deadline = time.monotonic() + HARD_BUDGET_SECONDS
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return []
    try:
        validator(source_url)
    except Exception:
        return []

    async with async_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36",
                java_script_enabled=True,
                accept_downloads=True,
            )
            source_page = await context.new_page()
            try:
                remaining = max(0.5, deadline - time.monotonic())
                await source_page.goto(source_url, wait_until="domcontentloaded", timeout=min(7000, int(remaining * 1000)))
                await source_page.wait_for_timeout(min(SERVER_SETTLE_MS, int(max(0, deadline - time.monotonic()) * 1000)))
                title = await source_page.title()
                try:
                    metadata = await source_page.locator("meta[property='og:title'], meta[name='twitter:title']").evaluate_all("els => els.map(el => el.content || '').join(' ')")
                except Exception:
                    metadata = ""
                try:
                    body_text = (await source_page.locator("body").inner_text(timeout=1500))[:12000]
                except Exception:
                    body_text = ""
                if not _identity_match(source_url, title, metadata, body_text):
                    print("🛡️ KRX18 Adapter: source identity evidence rejected", flush=True)
                    return []
                servers = await _extract_server_links(source_page, source_url, SERVER_LIMIT)
                if not servers:
                    print("🛡️ KRX18 Adapter: no public server/player links found", flush=True)
                    return []
                print(f"🎯 KRX18 Adapter: discovered {len(servers)} public server target(s)", flush=True)
            finally:
                await source_page.close()

            results: list[tuple[str, str]] = []
            for server_url in servers:
                if time.monotonic() >= deadline:
                    break
                page = await context.new_page()
                try:
                    remaining = max(0.5, deadline - time.monotonic())
                    await page.goto(server_url, wait_until="domcontentloaded", timeout=min(SERVER_PAGE_TIMEOUT_MS, int(remaining * 1000)), referer=source_url)
                    if time.monotonic() >= deadline:
                        break
                    try:
                        server_title = await page.title()
                        server_body = (await page.locator("body").inner_text(timeout=1200))[:8000]
                    except Exception:
                        server_title, server_body = "", ""
                    if not _identity_match(source_url, server_title, "", server_body):
                        print("🛡️ KRX18 Adapter: rejected server page with mismatched identity", flush=True)
                        continue
                    media = await _collect_server_media(page, validator, deadline)
                    for media_url in media:
                        if media_url not in [item[0] for item in results]:
                            results.append((media_url, server_url))
                    if results:
                        print(f"🎬 KRX18 Adapter: captured {len(results)} media candidate(s)", flush=True)
                except Exception as exc:
                    print(f"⚠️ KRX18 Adapter: server target failed ({type(exc).__name__})", flush=True)
                finally:
                    await page.close()
            return results[:MAX_MEDIA_CANDIDATES]
        finally:
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()


async def resolve(source_url: str, *, validator) -> list[tuple[str, str]]:
    if not is_krx18_url(source_url):
        return []
    try:
        return await asyncio.wait_for(_resolve_async(source_url, validator), timeout=HARD_BUDGET_SECONDS + 2.0)
    except asyncio.TimeoutError:
        print("⏱️ KRX18 Adapter: hard budget exhausted", flush=True)
        return []
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ KRX18 Adapter failed ({type(exc).__name__})", flush=True)
        return []


def install(bot_module) -> None:
    original_fallback = getattr(bot_module, "download_with_fallback", None)
    if not callable(original_fallback) or getattr(original_fallback, "_krx18_adapter", False):
        return
    browser_handoff = __import__("downloader.browser_download_handoff", fromlist=["resolve_to_file"])
    movie_guard = __import__("downloader.movie_source_guard", fromlist=["should_guard", "is_ad_host", "assess_local_media"])

    async def wrapped_fallback(*args, **kwargs):
        source_url = kwargs.get("url") or (args[0] if args else "")
        if not is_krx18_url(source_url):
            result = original_fallback(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        temp_dir = kwargs.get("temp_dir")
        is_audio = bool(kwargs.get("is_audio", False))
        if not temp_dir:
            return await original_fallback(*args, **kwargs)
        print("🎯 KRX18 Adapter: handling public movie page directly", flush=True)
        candidates = await resolve(source_url, validator=bot_module.validate_public_http_url)
        for candidate_url, referer_url in candidates:
            if is_audio:
                max_bytes = getattr(bot_module, "MAX_AUDIO_DOWNLOAD_BYTES", 500 * 1024 * 1024)
                min_bytes = 0
                min_duration = 0.0
            else:
                max_bytes = MAX_FILE_BYTES
                min_bytes = MIN_VIDEO_BYTES
                min_duration = MIN_VIDEO_DURATION
            try:
                path = await asyncio.to_thread(
                    browser_handoff.resolve_to_file,
                    candidate_url,
                    temp_dir,
                    validator=bot_module.validate_public_http_url,
                    is_audio=is_audio,
                    timeout_ms=30_000,
                    settle_ms=1_000,
                    max_file_bytes=max_bytes,
                    referer_url=referer_url,
                    min_video_bytes=min_bytes,
                    min_video_duration=min_duration,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ KRX18 Adapter: media handoff failed ({type(exc).__name__})", flush=True)
                path = None
            if path and os.path.isfile(path) and os.path.getsize(path) > 0:
                if not is_audio:
                    try:
                        if movie_guard.is_ad_host(candidate_url):
                            os.remove(path)
                            continue
                        if movie_guard.should_guard(source_url, is_audio=False):
                            gate = movie_guard.assess_local_media(
                                path,
                                source_url,
                                candidate_url=candidate_url,
                                is_audio=False,
                                min_bytes=MIN_VIDEO_BYTES,
                                min_duration=MIN_VIDEO_DURATION,
                            )
                            if not gate.accepted:
                                os.remove(path)
                                continue
                    except Exception as exc:
                        print(f"⚠️ KRX18 Adapter: candidate gate failed closed ({type(exc).__name__})", flush=True)
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                        continue
                size = os.path.getsize(path)
                print(f"✅ KRX18 Adapter: verified media ({size} bytes)", flush=True)
                return path, "KRX18 dedicated adapter: verified public media", "", {"status": "krx18_adapter_success", "candidate": candidate_url, "server": referer_url, "bytes_downloaded": size}
        print("❌ KRX18 Adapter: no verified public media candidate", flush=True)
        return None, "KRX18 dedicated adapter: no verified public media", "", {"status": "krx18_adapter_failed"}

    wrapped_fallback._krx18_adapter = True
    bot_module.download_with_fallback = wrapped_fallback
