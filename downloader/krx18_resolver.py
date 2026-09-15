"""Deterministic KRX18 public-page resolver.

The resolver follows only the public movie page's explicit Video Sources
(server/player) links and media exposed by those public player pages. It uses
identity evidence before accepting a media URL and fails closed when the
public page/player does not expose enough evidence.

It never bypasses authentication, CAPTCHA, DRM, paywalls, or other access
controls.
"""

from __future__ import annotations

import asyncio
import html
import re
from urllib.parse import unquote, urlparse

NON_SOURCE_HOSTS = {
    "onclckbn.net",
    "cdn.jsdelivr.net",
    "galleryn1.vcmdiawe.com",
    "bkcdn.net",
}

MEDIA_MARKERS = (".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".ts")
KRX18_RESOLVE_BUDGET_SECONDS = 32.0
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


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _host(value: str) -> str:
    try:
        return (urlparse(value).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _is_non_source_host(value: str) -> bool:
    host = _host(value)
    return any(host == suffix or host.endswith("." + suffix) for suffix in NON_SOURCE_HOSTS)


def _explicit_server_label(text: str, row: dict) -> bool:
    value = str(text or "").casefold()
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        return True
    for key in ("data_server", "data_player"):
        if isinstance(row.get(key), str) and row.get(key).strip():
            return True
    return bool(re.fullmatch(r"\s*(?:server|سيرفر)\s*\d*\s*", value))


def _score(text: str, href: str, row: dict) -> int:
    value = f"{text} {href}".casefold()
    score = 0
    if _explicit_server_label(text, row):
        score += 120
    if any(word in value for word in ("player", "watch", "source", "مشاهدة", "مشغل")):
        score += 18
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        score += 40
    if any(token in value for token in ("embed", "iframe")):
        score += 12
    return score


def extract_urls_from_onclick(value: str) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    urls = []
    for match in re.findall(r"https?://[^\s\"'<>\\]+", value, flags=re.I):
        candidate = match.rstrip("\\.,;)]}")
        if is_http_url(candidate):
            urls.append(candidate)
    return urls


def rank_targets(rows: list[dict], base_url: str, max_targets: int = 8) -> list[str]:
    """Return only explicit KRX18 Video Sources/server/player targets."""
    ranked: dict[str, int] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = " ".join(
            str(row.get(k) or "")
            for k in ("text", "attr", "onclick", "label", "data_server", "data_player")
        )
        if not _explicit_server_label(text, row):
            continue
        values: list[str] = []
        for key in (
            "href", "src", "data_server", "data_player", "data_download",
            "data_url", "data_href",
        ):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        values.extend(extract_urls_from_onclick(str(row.get("onclick") or "")))
        for href in values:
            if not is_http_url(href) or href == base_url:
                continue
            if _is_non_source_host(href):
                continue
            score = _score(text, href, row)
            if score <= 0:
                continue
            ranked[href] = max(score, ranked.get(href, 0))
    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, _ in ordered[:max_targets]]


def _movie_identity(source_url: str, title: str = "") -> tuple[str | None, set[str]]:
    parsed = urlparse(source_url)
    path = unquote(parsed.path or "")
    match = re.search(r"/movies/(?:([0-9]+)-)?([^/]+)/?", path, re.I)
    movie_id = match.group(1) if match else None
    slug = match.group(2) if match else ""
    raw = f"{slug} {title}".casefold()
    tokens = {
        token for token in re.split(r"[^a-z0-9]+", raw)
        if len(token) >= 3 and token not in {"movie", "movies", "full", "watch", "online", "free"}
    }
    if movie_id:
        tokens.add(movie_id)
    return movie_id, tokens


def identity_score(source_url: str, evidence_text: str, evidence_url: str = "", source_title: str = "") -> int:
    """Score only positive public identity evidence; no evidence means reject."""
    movie_id, tokens = _movie_identity(source_url, source_title)
    haystack = unquote(f"{evidence_text} {evidence_url}").casefold()
    score = 0
    if movie_id and movie_id in haystack:
        score += 100
    meaningful = {token for token in tokens if token != movie_id}
    overlap = sum(1 for token in meaningful if token in haystack)
    if overlap >= 3:
        score += 60
    elif overlap == 2:
        score += 40
    elif overlap == 1:
        score += 15
    return score


def _media_url(value: str) -> bool:
    if not is_http_url(value):
        return False
    if _is_non_source_host(value):
        return False
    parsed = urlparse(value)
    haystack = f"{parsed.path}?{parsed.query}".lower()
    return any(marker in haystack for marker in MEDIA_MARKERS) or any(
        marker in haystack for marker in ("stream", "playlist", "media", "source", "direct", "download")
    )


async def _safe_close(obj) -> None:
    try:
        await obj.close()
    except Exception:
        pass


async def _extract_source_targets(page, base_url: str) -> list[str]:
    """Extract only links inside the public page's Video Sources section."""
    script = r"""
    () => {
      const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,.title,.heading,strong,b,div')];
      const heading = headings.find(el => /video\s*sources?/i.test((el.innerText || el.textContent || '').trim()));
      if (!heading) return [];
      let container = heading;
      for (let i = 0; i < 4 && container; i++, container = container.parentElement) {
        const text = (container.innerText || container.textContent || '').trim();
        if (text.length > 20 && text.length < 12000 && /server\s*1/i.test(text)) break;
      }
      if (!container) return [];
      const rows = [...container.querySelectorAll('a[href],iframe[src],embed[src],[onclick],[data-server],[data-player],[data-url],[data-href]')];
      return rows.map(el => ({
        href: el.href || el.src || '',
        src: el.src || '',
        text: (el.innerText || el.textContent || '').trim(),
        attr: ((el.className || '') + ' ' + (el.id || '')).trim(),
        onclick: el.getAttribute('onclick') || '',
        data_server: el.getAttribute('data-server') || '',
        data_player: el.getAttribute('data-player') || '',
        data_url: el.getAttribute('data-url') || '',
        data_href: el.getAttribute('data-href') || ''
      }));
    }
    """
    try:
        rows = await page.evaluate(script)
    except Exception:
        return []
    return rank_targets(rows or [], base_url, max_targets=KRX18_MAX_SERVER_TARGETS)


async def _collect_public_media(page, validator, candidates: dict[str, tuple[int, int, str]], source_url: str, source_title: str, trusted_target: str) -> None:
    """Collect media only after target-page identity is established."""
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        body = await page.locator("body").inner_text(timeout=1200)
    except Exception:
        body = ""
    evidence = f"{title}\n{body[:12000]}"
    score_identity = identity_score(source_url, evidence, page.url, source_title)
    if score_identity < 40:
        return

    async def add(value: str, content_type: str = "") -> None:
        if not _media_url(value):
            return
        try:
            validator(value)
        except Exception:
            return
        score = 100 + score_identity
        if "mpegurl" in content_type.lower() or value.lower().split("?", 1)[0].endswith(".m3u8"):
            score += 20
        if value not in candidates or score > candidates[value][0]:
            candidates[value] = (score, score_identity, trusted_target)

    try:
        rows = await page.locator("video,audio,source").evaluate_all("""els => els.map(el => ({src: el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || el.getAttribute('data-url') || '', type: el.getAttribute('type') || ''}))""")
    except Exception:
        rows = []
    for row in rows or []:
        if isinstance(row, dict):
            await add(str(row.get("src") or ""), str(row.get("type") or ""))

    try:
        scripts = await page.locator("script").all_text_contents()
    except Exception:
        scripts = []
    pattern = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
    for script_text in scripts or []:
        decoded = html.unescape(script_text).replace("\\/", "/")
        for value in pattern.findall(decoded):
            value = value.rstrip("\\.,;)]}")
            if _media_url(value):
                await add(value)

    for frame in list(page.frames):
        if frame is page.main_frame:
            continue
        try:
            frame_title = await frame.title()
        except Exception:
            frame_title = ""
        try:
            frame_text = await frame.locator("body").inner_text(timeout=800)
        except Exception:
            frame_text = ""
        frame_score = identity_score(source_url, f"{frame_title}\n{frame_text[:8000]}", frame.url, source_title)
        if frame_score < 40:
            continue
        try:
            frame_rows = await frame.locator("video,audio,source").evaluate_all("""els => els.map(el => ({src: el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || el.getAttribute('data-url') || '', type: el.getAttribute('type') || ''}))""")
        except Exception:
            frame_rows = []
        for row in frame_rows or []:
            if isinstance(row, dict):
                await add(str(row.get("src") or ""), str(row.get("type") or ""))


async def _resolve_media_async(url: str, *, validator, max_candidates: int = KRX18_MAX_CANDIDATES) -> list[str]:
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return []
    try:
        validator(url)
    except Exception:
        return []

    async with async_playwright() as playwright:
        browser = None
        context = None
        source_page = None
        try:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36", java_script_enabled=True)
            source_page = await context.new_page()
            await source_page.goto(url, wait_until="domcontentloaded", timeout=KRX18_SERVER_TIMEOUT_MS)
            await source_page.wait_for_timeout(KRX18_SETTLE_MS)
            source_title = await source_page.title()
            targets = await _extract_source_targets(source_page, url)
            if not targets:
                print("🛡️ KRX18 Dedicated Resolver: no explicit Video Sources targets", flush=True)
                return []
            print(f"🎯 KRX18 Dedicated Resolver: {len(targets)} public server target(s)", flush=True)
            candidates: dict[str, tuple[int, int, str]] = {}
            for target in targets:
                page = None
                try:
                    page = await context.new_page()
                    await page.goto(target, wait_until="domcontentloaded", timeout=KRX18_SERVER_TIMEOUT_MS)
                    await page.wait_for_timeout(KRX18_SETTLE_MS)
                    await _collect_public_media(page, validator, candidates, url, source_title, target)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"⚠️ KRX18 Dedicated Resolver: server failed ({type(exc).__name__})", flush=True)
                finally:
                    if page is not None:
                        await _safe_close(page)
            ranked = sorted(candidates.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))
            if ranked:
                best_identity = ranked[0][1][1]
                print(f"✅ KRX18 Dedicated Resolver: {len(ranked)} identity-verified media candidate(s), identity={best_identity}", flush=True)
                return [item[0] for item in ranked[:max_candidates]]
            print("🛡️ KRX18 Dedicated Resolver: no identity-verified public media", flush=True)
            return []
        finally:
            if source_page is not None:
                await _safe_close(source_page)
            if context is not None:
                await _safe_close(context)
            if browser is not None:
                await _safe_close(browser)


async def resolve_media(url: str, *, validator, max_candidates: int = KRX18_MAX_CANDIDATES) -> list[str]:
    """Resolve KRX18 only from public Video Sources/server/player pages."""
    if not is_krx18_url(url):
        return []
    try:
        return await asyncio.wait_for(
            _resolve_media_async(url, validator=validator, max_candidates=max_candidates),
            timeout=KRX18_RESOLVE_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        print("⏱️ KRX18 Dedicated Resolver: hard budget exhausted", flush=True)
        return []
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ KRX18 Dedicated Resolver failed closed: {type(exc).__name__}", flush=True)
        return []
