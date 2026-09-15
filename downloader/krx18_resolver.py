"""Deterministic KRX18 public-page resolver.

Follows only explicit public Video Sources/server links belonging to the
requested movie. It never bypasses authentication, CAPTCHA, DRM, paywalls,
or other access controls.
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
MEDIA_CONTENT_TYPES = (
    "video/",
    "audio/",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
)
KRX18_RESOLVE_BUDGET_SECONDS = 30.0
KRX18_SERVER_TIMEOUT_MS = 7000
KRX18_SETTLE_MS = 600
KRX18_PLAYER_SETTLE_MS = 1800
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
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except Exception:
        return False


def _blocked_host(value: str) -> bool:
    host = _host(value)
    return any(host == item or host.endswith("." + item) for item in NON_SOURCE_HOSTS)


def _is_media_response_url(value: str, content_type: str = "") -> bool:
    if not _http(value) or _blocked_host(value):
        return False
    parsed = urlparse(value)
    haystack = f"{parsed.path}?{parsed.query}".lower()
    ctype = str(content_type or "").lower().split(";", 1)[0].strip()
    if any(marker in haystack for marker in MEDIA_MARKERS):
        return True
    return any(ctype.startswith(marker) or ctype == marker for marker in MEDIA_CONTENT_TYPES)


def _media_url(value: str) -> bool:
    if not _http(value) or _blocked_host(value):
        return False
    parsed = urlparse(value)
    haystack = f"{parsed.path}?{parsed.query}".lower()
    return any(marker in haystack for marker in MEDIA_MARKERS) or any(
        marker in haystack for marker in ("stream", "playlist", "media", "source", "direct", "download")
    )


def extract_urls_from_onclick(value: str) -> list[str]:
    if not isinstance(value, str):
        return []
    output = []
    for match in re.findall(r"https?://[^\s\"'<>\\]+", value, re.I):
        candidate = match.rstrip("\\.,;)]}")
        if _http(candidate):
            output.append(candidate)
    return output


def rank_targets(rows: list[dict], base_url: str, max_targets: int = 8) -> list[str]:
    ranked = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        label = " ".join(
            str(row.get(key) or "")
            for key in ("text", "attr", "onclick", "label", "data_server", "data_player")
        )
        if not re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", label, re.I):
            continue
        score = 120 + (20 if any(x in label.casefold() for x in ("player", "watch", "source", "embed", "iframe")) else 0)
        values = []
        for key in ("href", "src", "data_server", "data_player", "data_download", "data_url", "data_href"):
            raw = row.get(key)
            if isinstance(raw, str) and raw.strip():
                values.append(raw.strip())
        values.extend(extract_urls_from_onclick(str(row.get("onclick") or "")))
        for raw in values:
            candidates = re.findall(r"https?://[^\s\"'<>\\]+", raw, re.I) or [raw]
            for candidate in candidates:
                candidate = html.unescape(candidate).rstrip("\\.,;)]}")
                if not _http(candidate) or candidate == base_url or _blocked_host(candidate):
                    continue
                if any(marker in candidate.lower() for marker in MEDIA_MARKERS):
                    continue
                ranked[candidate] = max(score, ranked.get(candidate, 0))
    return [url for url, _ in sorted(ranked.items(), key=lambda item: (-item[1], item[0]))[:max_targets]]


def _movie_tokens(source_url: str, title: str = ""):
    path = unquote(urlparse(source_url).path or "")
    match = re.search(r"/movies/(?:([0-9]+)-)?([^/]+)/?$", path, re.I)
    movie_id = match.group(1) if match else None
    slug = match.group(2) if match else ""
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", f"{slug} {title}".casefold())
        if len(token) >= 3
    }
    if movie_id:
        tokens.add(movie_id)
    return movie_id, tokens


def identity_score(
    source_url: str,
    evidence_text: str,
    evidence_url: str = "",
    source_title: str = "",
    *,
    explicit_server_provenance: bool = False,
) -> int:
    movie_id, tokens = _movie_tokens(source_url, source_title)
    haystack = re.sub(r"\s+", " ", unquote(f"{evidence_text} {evidence_url}")).casefold()
    score = 70 if explicit_server_provenance else 0
    if movie_id and movie_id in haystack:
        score += 100
    meaningful = {token for token in tokens if token != movie_id}
    overlap = sum(1 for token in meaningful if token in haystack)
    score += 60 if overlap >= 3 else 40 if overlap == 2 else 15 if overlap == 1 else 0
    title = re.sub(r"\s+", " ", unquote(source_title or "")).strip().casefold()
    if len(title) >= 8 and title not in {"krx18", "krx18.com"} and title in haystack:
        score += 50
    return score


async def _safe_close(obj):
    try:
        await obj.close()
    except Exception:
        pass


async def _extract_source_targets(page, base_url):
    script = """() => [...document.querySelectorAll('a[href],iframe[src],embed[src],[onclick],[data-server],[data-player],[data-url],[data-href]')].map(el=>({href:el.href||el.src||'',text:(el.innerText||el.textContent||'').trim(),onclick:el.getAttribute('onclick')||'',data_server:el.getAttribute('data-server')||'',data_player:el.getAttribute('data-player')||'',data_url:el.getAttribute('data-url')||'',data_href:el.getAttribute('data-href')||'',cls:`${el.className||''} ${el.id||''}`}))"""
    try:
        rows = await page.evaluate(script)
    except Exception:
        return []
    return rank_targets(rows or [], base_url, KRX18_MAX_SERVER_TARGETS)


async def _collect_media(page, source_url, source_title, target, candidates, network_media=None):
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        body = await page.locator("body").inner_text(timeout=1200)
    except Exception:
        body = ""
    score = identity_score(
        source_url,
        f"{title}\n{body[:12000]}",
        f"{page.url} {target}",
        source_title,
        explicit_server_provenance=True,
    )
    if score < 70:
        return

    media = list(network_media or [])
    try:
        media.extend(
            await page.locator("video,audio,source").evaluate_all(
                """els=>els.map(el=>el.currentSrc||el.src||el.getAttribute('src')||el.getAttribute('data-src')||el.getAttribute('data-url')||'')"""
            )
        )
    except Exception:
        pass
    try:
        for script in await page.locator("script").all_text_contents():
            media.extend(
                re.findall(
                    r"https?://[^\s\"'<>\\]+",
                    html.unescape(script).replace("\\/", "/"),
                    re.I,
                )
            )
    except Exception:
        pass
    for frame in list(page.frames):
        if frame is page.main_frame:
            continue
        try:
            frame_title = await frame.title()
            frame_body = await frame.locator("body").inner_text(timeout=800)
            if identity_score(
                source_url,
                f"{frame_title}\n{frame_body[:8000]}",
                f"{frame.url} {target}",
                source_title,
                explicit_server_provenance=True,
            ) < 70:
                continue
            media.extend(
                await frame.locator("video,audio,source").evaluate_all(
                    """els=>els.map(el=>el.currentSrc||el.src||el.getAttribute('src')||el.getAttribute('data-src')||el.getAttribute('data-url')||'')"""
                )
            )
        except Exception:
            continue
    for value in media:
        if isinstance(value, str) and _media_url(value):
            candidates.setdefault(value, (score, target))


async def _resolve_media_async(
    url: str,
    *,
    validator,
    request_factory=None,
    open_function=None,
    read_function=None,
    max_candidates: int = KRX18_MAX_CANDIDATES,
) -> list[str]:
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
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36",
                java_script_enabled=True,
            )
            source_page = await context.new_page()
            source_title = ""
            targets = []
            try:
                remaining = max(0.5, deadline - time.monotonic())
                await source_page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=min(KRX18_SERVER_TIMEOUT_MS, int(remaining * 1000)),
                )
                source_title = await source_page.title()
                targets = await _extract_source_targets(source_page, url)
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
                    source_title = wp_title or source_title
                    targets = wp_targets
                    if targets:
                        print(
                            f"🌐 KRX18 Public WordPress: discovered {len(targets)} explicit server target(s)",
                            flush=True,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(
                        f"🛡️ KRX18 Public WordPress: unavailable ({type(exc).__name__})",
                        flush=True,
                    )

            if not targets:
                print("🛡️ KRX18 Dedicated Resolver: no explicit public Video Sources targets", flush=True)
                return []

            print(f"🎯 KRX18 Dedicated Resolver: {len(targets)} public server target(s)", flush=True)
            candidates = {}

            for target in targets[:KRX18_MAX_SERVER_TARGETS]:
                if time.monotonic() >= deadline:
                    break
                page = None
                network_media = []

                def on_response(response):
                    try:
                        content_type = response.headers.get("content-type", "")
                        response_url = response.url
                        if _is_media_response_url(response_url, content_type):
                            network_media.append(response_url)
                    except Exception:
                        pass

                try:
                    remaining = max(0.5, deadline - time.monotonic())
                    page = await context.new_page()
                    page.on("response", on_response)
                    await page.goto(
                        target,
                        wait_until="domcontentloaded",
                        timeout=min(KRX18_SERVER_TIMEOUT_MS, int(remaining * 1000)),
                    )
                    remaining = max(0.2, deadline - time.monotonic())
                    await page.wait_for_timeout(
                        min(KRX18_PLAYER_SETTLE_MS, int(remaining * 1000))
                    )
                    await _collect_media(
                        page,
                        url,
                        source_title,
                        target,
                        candidates,
                        network_media=network_media,
                    )
                    if network_media:
                        print(
                            f"🎥 KRX18 Dedicated Resolver: server exposed {len(network_media)} media response(s)",
                            flush=True,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(
                        f"⚠️ KRX18 Dedicated Resolver: server failed ({type(exc).__name__})",
                        flush=True,
                    )
                finally:
                    if page is not None:
                        await _safe_close(page)

            ranked = sorted(candidates.items(), key=lambda item: (-item[1][0], item[0]))
            result = [candidate for candidate, _ in ranked[:max_candidates]]
            print(
                f"✅ KRX18 Dedicated Resolver: {len(result)} provenance-verified public media candidate(s)"
                if result
                else "🛡️ KRX18 Dedicated Resolver: no identity-verified public media",
                flush=True,
            )
            return result
        finally:
            if context is not None:
                await _safe_close(context)
            if browser is not None:
                await _safe_close(browser)


async def resolve_media(url: str, **kwargs) -> list[str]:
    return await _resolve_media_async(url, **kwargs) if is_krx18_url(url) else []
