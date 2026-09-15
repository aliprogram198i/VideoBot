"""Deterministic KRX18 public-page resolver.

Only public Video Sources/server links from the requested movie page (or its
public WordPress representation) are followed. Media is accepted only when
it remains attributable to one of those explicit server targets. No login,
CAPTCHA, DRM, paywall, or other access-control bypass is attempted.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from urllib.parse import unquote, urlparse

from .krx18_wp_public_sources import fetch_public_post

NON_SOURCE_HOSTS = {"onclckbn.net", "cdn.jsdelivr.net", "vcmdiawe.com", "bkcdn.net"}
MEDIA_MARKERS = (".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".ts")
KRX18_RESOLVE_BUDGET_SECONDS = 30.0
KRX18_SERVER_TIMEOUT_MS = 7_000
KRX18_SETTLE_MS = 600
KRX18_MAX_SERVER_TARGETS = 3
KRX18_MAX_CANDIDATES = 8


def is_krx18_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host == "krx18.com" or host.endswith(".krx18.com")


def _host(value: str) -> str:
    try:
        return (urlparse(value).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _http(value: str) -> bool:
    try:
        p = urlparse(value)
        return p.scheme in {"http", "https"} and bool(p.hostname)
    except Exception:
        return False


def _blocked_host(value: str) -> bool:
    host = _host(value)
    return any(host == item or host.endswith("." + item) for item in NON_SOURCE_HOSTS)


def _media_url(value: str) -> bool:
    if not _http(value) or _blocked_host(value):
        return False
    p = urlparse(value)
    haystack = f"{p.path}?{p.query}".lower()
    return any(marker in haystack for marker in MEDIA_MARKERS) or any(
        marker in haystack for marker in ("stream", "playlist", "media", "source", "direct", "download")
    )


def _movie_tokens(source_url: str, title: str = "") -> tuple[str | None, set[str]]:
    path = unquote(urlparse(source_url).path or "")
    match = re.search(r"/movies/(?:([0-9]+)-)?([^/]+)/?$", path, re.I)
    movie_id = match.group(1) if match else None
    slug = match.group(2) if match else ""
    raw = f"{slug} {title}".casefold()
    tokens = {x for x in re.split(r"[^a-z0-9]+", raw) if len(x) >= 3}
    if movie_id:
        tokens.add(movie_id)
    return movie_id, tokens


def identity_score(source_url: str, evidence_text: str, evidence_url: str = "", source_title: str = "", *, explicit_server_provenance: bool = False) -> int:
    """Score public identity/provenance without accepting unrelated media."""
    movie_id, tokens = _movie_tokens(source_url, source_title)
    haystack = re.sub(r"\s+", " ", unquote(f"{evidence_text} {evidence_url}")).casefold()
    score = 70 if explicit_server_provenance else 0
    if movie_id and movie_id in haystack:
        score += 100
    meaningful = {x for x in tokens if x != movie_id}
    overlap = sum(1 for x in meaningful if x in haystack)
    if overlap >= 3:
        score += 60
    elif overlap == 2:
        score += 40
    elif overlap == 1:
        score += 15
    normalized_title = re.sub(r"\s+", " ", unquote(source_title or "")).strip().casefold()
    if len(normalized_title) >= 8 and normalized_title not in {"krx18", "krx18.com"} and normalized_title in haystack:
        score += 50
    return score


def _extract_script_urls(text: str) -> list[str]:
    decoded = html.unescape(text or "").replace("\\/", "/")
    urls = []
    for value in re.findall(r"https?://[^\s\"'<>\\]+", decoded, re.I):
        value = value.rstrip("\\.,;)]}")
        if _media_url(value):
            urls.append(value)
    return urls


async def _safe_close(obj) -> None:
    try:
        await obj.close()
    except Exception:
        pass


async def _extract_dom_media(page) -> list[str]:
    try:
        rows = await page.locator("video,audio,source").evaluate_all(
            """els => els.map(el => el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || el.getAttribute('data-url') || '')"""
        )
    except Exception:
        return []
    return [value for value in rows or [] if isinstance(value, str) and _media_url(value)]


async def _collect_media(page, source_url: str, source_title: str, target: str, candidates: dict[str, tuple[int, str]]) -> None:
    try:
        page_title = await page.title()
    except Exception:
        page_title = ""
    try:
        body = await page.locator("body").inner_text(timeout=1200)
    except Exception:
        body = ""
    evidence = f"{page_title}\n{body[:12000]}\n{target}"
    score = identity_score(source_url, evidence, f"{page.url} {target}", source_title, explicit_server_provenance=True)
    # The target itself came from the requested movie's explicit Video Sources.
    # 70 is the provenance floor; semantic evidence raises confidence but is not
    # required when the player page exposes no movie title/id of its own.
    if score < 70:
        return

    media = await _extract_dom_media(page)
    try:
        scripts = await page.locator("script").all_text_contents()
    except Exception:
        scripts = []
    for script in scripts or []:
        media.extend(_extract_script_urls(script))

    for frame in list(page.frames):
        if frame is page.main_frame:
            continue
        try:
            frame_title = await frame.title()
            frame_text = await frame.locator("body").inner_text(timeout=800)
            frame_score = identity_score(source_url, f"{frame_title}\n{frame_text[:8000]}", f"{frame.url} {target}", source_title, explicit_server_provenance=True)
            if frame_score < 70:
                continue
            rows = await frame.locator("video,audio,source").evaluate_all(
                """els => els.map(el => el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || el.getAttribute('data-url') || '')"""
            )
            media.extend([value for value in rows or [] if isinstance(value, str)])
            scripts = await frame.locator("script").all_text_contents()
            for script in scripts or []:
                media.extend(_extract_script_urls(script))
        except Exception:
            continue

    for value in media:
        if not _media_url(value):
            continue
        if _blocked_host(value):
            continue
        previous = candidates.get(value)
        row = (score, target)
        if previous is None or row[0] > previous[0]:
            candidates[value] = row


async def _resolve_media_async(url: str, *, validator, request_factory=None, open_function=None, read_function=None, max_candidates: int = KRX18_MAX_CANDIDATES) -> list[str]:
    try:
        validator(url)
    except Exception:
        return []
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return []

    deadline = time.monotonic() + KRX18_RESOLVE_BUDGET_SECONDS
    async with async_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                java_script_enabled=True,
            )
            source_page = await context.new_page()
            source_title = ""
            targets: list[str] = []
            try:
                remaining = max(0.5, deadline - time.monotonic())
                await source_page.goto(url, wait_until="domcontentloaded", timeout=min(KRX18_SERVER_TIMEOUT_MS, int(remaining * 1000)))
                await source_page.wait_for_timeout(min(KRX18_SETTLE_MS, max(0, int((deadline - time.monotonic()) * 1000))))
                source_title = await source_page.title()
                # The dedicated browser path is authoritative when it exposes
                # explicit server links; otherwise use the bounded public WP path.
                try:
                    targets = await _extract_source_targets(source_page, url)
                except Exception:
                    targets = []
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                await _safe_close(source_page)

            if not targets and callable(request_factory) and callable(open_function) and callable(read_function):
                try:
                    wp_title, wp_targets = await asyncio.to_thread(
                        fetch_public_post,
                        url,
                        request_factory=request_factory,
                        open_function=open_function,
                        read_function=read_function,
                    )
                    if wp_title:
                        source_title = wp_title
                    targets = wp_targets
                    if targets:
                        print(f"🌐 KRX18 Public WordPress: discovered {len(targets)} explicit server target(s)", flush=True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"🛡️ KRX18 Public WordPress: unavailable ({type(exc).__name__})", flush=True)

            if not targets:
                print("🛡️ KRX18 Dedicated Resolver: no explicit public Video Sources targets", flush=True)
                return []

            print(f"🎯 KRX18 Dedicated Resolver: {len(targets)} public server target(s)", flush=True)
            candidates: dict[str, tuple[int, str]] = {}
            for target in targets[:KRX18_MAX_SERVER_TARGETS]:
                if time.monotonic() >= deadline:
                    break
                page = None
                try:
                    remaining = max(0.5, deadline - time.monotonic())
                    page = await context.new_page()
                    await page.goto(target, wait_until="domcontentloaded", timeout=min(KRX18_SERVER_TIMEOUT_MS, int(remaining * 1000)))
                    remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                    if remaining_ms:
                        await page.wait_for_timeout(min(KRX18_SETTLE_MS, remaining_ms))
                    await _collect_media(page, url, source_title, target, candidates)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"⚠️ KRX18 Dedicated Resolver: server failed ({type(exc).__name__})", flush=True)
                finally:
                    if page is not None:
                        await _safe_close(page)

            ranked = sorted(candidates.items(), key=lambda item: (-item[1][0], item[0]))
            result = [url for url, _ in ranked[:max_candidates]]
            if result:
                print(f"✅ KRX18 Dedicated Resolver: {len(result)} provenance-verified public media candidate(s)", flush=True)
            else:
                print("🛡️ KRX18 Dedicated Resolver: no identity-verified public media", flush=True)
            return result
        finally:
            if context is not None:
                await _safe_close(context)
            if browser is not None:
                await _safe_close(browser)


async def resolve_media(url: str, **kwargs) -> list[str]:
    if not is_krx18_url(url):
        return []
    return await _resolve_media_async(url, **kwargs)
