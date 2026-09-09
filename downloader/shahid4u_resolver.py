"""Site-specific public-link discovery for Shahid4u pages.

This adapter only follows download/server links that the Shahid4u page
itself exposes. It does not bypass authentication, CAPTCHA, DRM, or other
access controls. The returned URLs are handed to the existing browser
materialization layer, which performs the actual bounded download.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request


HOST_SUFFIXES = ("shahid4u.run",)
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_CANDIDATES = 16
MAX_BROWSER_CANDIDATES = 20
TIMEOUT_SECONDS = 25

_DOWNLOAD_TERMS = (
    "download", "تحميل", "تحميل مباشر", "تنزيل", "direct", "رابط التحميل",
)
_QUALITY_RE = re.compile(r"(?:2160|1440|1080|720|480|360|240)\s*p", re.I)


def _is_shahid4u(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in HOST_SUFFIXES)


def _is_http(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _score(text: str, href: str) -> int:
    value = f"{text} {href}".casefold()
    score = 0
    if any(term in value for term in _DOWNLOAD_TERMS):
        score += 100
    if "secure_stream" in value or "direct_stream" in value:
        score += 85
    if "mycima" in value:
        score += 70
    quality = _QUALITY_RE.search(value)
    if quality:
        score += {"2160": 60, "1440": 55, "1080": 50, "720": 40, "480": 30, "360": 20, "240": 10}.get(quality.group(0)[:-1], 0)
    if ".mp4" in value or ".m3u8" in value or ".mpd" in value:
        score += 40
    return score


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[tuple[int, str, str]] = []
        self._stack: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        data = {str(k).lower(): str(v or "") for k, v in attrs}
        href = data.get("href", "").strip()
        if not href:
            return
        absolute = urljoin(self.base_url, html_lib.unescape(href))
        if _is_http(absolute):
            self._stack.append({"href": absolute, "text": ""})

    def handle_data(self, data: str):
        if self._stack:
            self._stack[-1]["text"] += " " + data.strip()

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._stack:
            item = self._stack.pop()
            text = re.sub(r"\s+", " ", item["text"]).strip()
            self.links.append((_score(text, item["href"]), item["href"], text))


def _extract_urls_from_html(page_url: str, body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="ignore")
    parser = _LinkParser(page_url)
    try:
        parser.feed(text)
    except Exception:
        pass

    ranked: dict[str, int] = {}
    for score, href, _label in parser.links:
        if score >= 60:
            ranked[href] = max(score, ranked.get(href, 0))

    # Some themes store the server URL in attributes/inline JS instead of a
    # normal anchor. Only retain public HTTP(S) URLs and require a Shahid4u
    # download marker or a known downstream host marker.
    for raw in re.findall(r"https?://[^\"'<>\\s]+", text, flags=re.I):
        candidate = html_lib.unescape(raw).rstrip("),;\\")
        score = _score("", candidate)
        if score >= 60 and _is_http(candidate):
            ranked[candidate] = max(score, ranked.get(candidate, 0))

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, _score_value in ordered[:MAX_CANDIDATES]]


def _fetch_page(url: str, *, validator, request_factory, open_function, read_function) -> bytes:
    validator(url)
    request = request_factory(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ar,en;q=0.8",
        },
    )
    with open_function(request, timeout=TIMEOUT_SECONDS, max_bytes=MAX_HTML_BYTES) as response:
        return read_function(response, MAX_HTML_BYTES)


async def _browser_discover(url: str, *, validator) -> list[str]:
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return []

    found: dict[str, int] = {}
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception:
            return []
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36",
                java_script_enabled=True,
                accept_downloads=True,
            )
            page = await context.new_page()

            def remember(candidate: str, extra: int = 0) -> None:
                if not _is_http(candidate):
                    return
                try:
                    validator(candidate)
                except Exception:
                    return
                found[candidate] = max(found.get(candidate, 0), _score("", candidate) + extra)

            async def on_download(download) -> None:
                try:
                    remember(download.url, 180)
                except Exception:
                    pass

            async def on_response(response) -> None:
                try:
                    ct = (response.headers.get("content-type") or "").lower()
                    if ct.startswith("video/") or ct.startswith("audio/") or "mpegurl" in ct or "dash+xml" in ct:
                        remember(response.url, 160)
                except Exception:
                    pass

            page.on("download", on_download)
            page.on("response", on_response)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            except Exception:
                pass
            await page.wait_for_timeout(1800)

            try:
                rows = await page.locator("a[href], iframe[src], embed[src]").evaluate_all(
                    """els => els.map(el => ({href: el.href || el.src || '', text: (el.innerText || el.textContent || '').trim(), attr: ((el.className || '') + ' ' + (el.id || '') + ' ' + (el.getAttribute('data-server') || '') + ' ' + (el.getAttribute('data-download') || '')).trim()}))"""
                )
            except Exception:
                rows = []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                href = str(row.get("href") or "")
                text = f"{row.get('text') or ''} {row.get('attr') or ''}"
                score = _score(text, href)
                if score >= 60:
                    remember(href, score)

            # Explicitly click only download/server controls exposed by the
            # page. This can trigger a public browser download or media request.
            selector = "a[href], button, [role='button']"
            try:
                rows = await page.locator(selector).evaluate_all(
                    """els => els.map((el,index) => ({index, href: el.href || '', text: (el.innerText || el.textContent || '').trim(), attr: ((el.className || '') + ' ' + (el.id || '') + ' ' + (el.getAttribute('data-server') || '') + ' ' + (el.getAttribute('data-download') || '')).trim()}))"""
                )
            except Exception:
                rows = []
            ranked_controls = []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                score = _score(f"{row.get('text') or ''} {row.get('attr') or ''}", str(row.get('href') or ""))
                if score >= 100:
                    try:
                        ranked_controls.append((score, int(row.get("index"))))
                    except (TypeError, ValueError):
                        pass
            locator = page.locator(selector)
            for _score_value, index in sorted(ranked_controls, reverse=True)[:8]:
                try:
                    await locator.nth(index).click(timeout=1800, no_wait_after=True)
                    await page.wait_for_timeout(700)
                except Exception:
                    continue

            try:
                dom_media = await page.locator("video, audio, source").evaluate_all(
                    """els => els.map(el => el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || '')"""
                )
            except Exception:
                dom_media = []
            for candidate in dom_media or []:
                remember(str(candidate), 150)

            await context.close()
        finally:
            await browser.close()

    return [url for url, _score_value in sorted(found.items(), key=lambda item: (-item[1], item[0]))[:MAX_BROWSER_CANDIDATES]]


def resolve(url: str, *, validator, request_factory=Request, open_function=None, read_function=None) -> list[str]:
    """Return public download/server candidates exposed by a Shahid4u page."""
    if not _is_shahid4u(url):
        return []

    candidates: list[str] = []
    if open_function is not None and read_function is not None:
        try:
            body = _fetch_page(url, validator=validator, request_factory=request_factory, open_function=open_function, read_function=read_function)
            candidates.extend(_extract_urls_from_html(url, body))
        except Exception as exc:
            print(f"⚠️ Shahid4u Resolver: static page discovery failed ({type(exc).__name__})", flush=True)

    if candidates:
        print(f"🎯 Shahid4u Resolver: discovered {len(candidates)} download/server candidate(s)", flush=True)
        return candidates

    try:
        candidates = asyncio.run(_browser_discover(url, validator=validator))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            candidates = loop.run_until_complete(_browser_discover(url, validator=validator))
        finally:
            loop.close()
    except Exception as exc:
        print(f"⚠️ Shahid4u Resolver: browser discovery failed ({type(exc).__name__})", flush=True)
        candidates = []

    if candidates:
        print(f"🎯 Shahid4u Resolver: browser discovered {len(candidates)} candidate(s)", flush=True)
    else:
        print("🎯 Shahid4u Resolver: no public download candidate found", flush=True)
    return candidates
