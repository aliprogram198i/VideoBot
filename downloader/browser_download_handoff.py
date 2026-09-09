"""Bounded handoff for browser-triggered public media downloads."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 45_000
DEFAULT_SETTLE_MS = 2_000
DEFAULT_MAX_PAGES = 4
DEFAULT_MAX_MEDIA_RESPONSES = 80
DEFAULT_MAX_CLICKS = 8
DEFAULT_MAX_FILE_BYTES = 500 * 1024 * 1024

_MEDIA_FILE_EXTENSIONS = (
    ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".flv", ".wmv",
    ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac",
)


def _browser_enabled() -> bool:
    return os.getenv("ALIBOT_BROWSER_RESOLVER", "1").strip().lower() not in {"0", "false", "no", "off"}


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _safe_filename(name: str, is_audio: bool) -> str:
    clean = Path(name or "media").name.replace("\x00", "_")
    if not clean or clean in {".", ".."}: clean = "audio" if is_audio else "video"
    return clean[:180]


def _looks_like_media(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _MEDIA_FILE_EXTENSIONS)


def _is_media_response(url: str, content_type: str | None, disposition: str | None) -> bool:
    ct = (content_type or "").lower()
    cd = (disposition or "").lower()
    if "attachment" in cd: return True
    if ct.startswith("video/") or ct.startswith("audio/"): return True
    return _looks_like_media(url)


def _score_control(text: str, href: str) -> int:
    hay = f"{text} {href}".lower()
    score = 0
    for token, weight in (("download", 8), ("تحميل", 8), ("server", 5), ("سيرفر", 5),
                          ("player", 4), ("مشاهدة", 3), ("watch", 3), ("play", 2)):
        if token in hay: score += weight
    return score


def _is_download_target(text: str, href: str) -> bool:
    hay = f"{text} {href}".lower()
    return any(token in hay for token in ("download", "تحميل", "direct", "تنزيل"))


def _navigation_score(text: str, href: str) -> int:
    return _score_control(text, href)


async def _candidate_links(page, page_url: str) -> list[str]:
    try:
        rows = await page.locator("a[href], iframe[src], embed[src]").evaluate_all(
            """els => els.map((el,index) => ({index, href: el.href || el.src || '', text: (el.innerText || el.textContent || '').trim(), attr: ((el.className || '') + ' ' + (el.id || '')).trim()}))"""
        )
    except Exception:
        return []
    base = urlparse(page_url)
    ranked: list[tuple[int, int, str]] = []
    for row in rows or []:
        if not isinstance(row, dict): continue
        link = str(row.get("href") or "")
        if not _is_http_url(link): continue
        parsed = urlparse(link)
        score = 10 if parsed.netloc == base.netloc else 2
        score += _score_control(str(row.get("text") or ""), link)
        if _is_download_target(str(row.get("text") or ""), link): score += 30
        if _looks_like_media(link): score += 20
        if score >= 10: ranked.append((score, int(row.get("index") or 0), link))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:16]]


def _stream_with_browser_context(url: str, output_dir: str, *, cookies: list[dict], referer_url: str | None, is_audio: bool, max_file_bytes: int, timeout_s: float) -> str | None:
    if not _is_http_url(url): return None
    cookie_header = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies if c.get('name'))
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
        "Accept": "*/*",
    }
    if cookie_header: headers["Cookie"] = cookie_header
    if referer_url: headers["Referer"] = referer_url
    parsed = urlparse(url)
    if parsed.scheme == "https": headers.setdefault("Origin", f"https://{parsed.netloc}")
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in _MEDIA_FILE_EXTENSIONS: suffix = ".m4a" if is_audio else ".mp4"
    fd, target = tempfile.mkstemp(prefix="browser_", suffix=suffix, dir=output_dir)
    os.close(fd)
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=timeout_s) as response, open(target, "wb") as output:
            total = 0
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_file_bytes: return None
                except ValueError: pass
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk: break
                total += len(chunk)
                if total > max_file_bytes: return None
                output.write(chunk)
        if total > 0:
            print(f"🌐 Browser Download Handoff: streamed {total} bytes", flush=True)
            return target
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        print(f"⚠️ Browser Download Handoff: stream failed ({type(exc).__name__})", flush=True)
    except Exception as exc:
        print(f"⚠️ Browser Download Handoff: stream failed ({type(exc).__name__})", flush=True)
    try: os.remove(target)
    except OSError: pass
    return None


async def _click_controls(page, max_clicks: int) -> None:
    selector = "a[href], button, [role='button'], input[type='button'], input[type='submit']"
    try:
        rows = await page.locator(selector).evaluate_all(
            """els => els.map((el,index) => ({index, href: el.href || '', text: (el.innerText || el.textContent || el.value || '').trim(), attr: ((el.className || '') + ' ' + (el.id || '') + ' ' + (el.getAttribute('data-server') || '') + ' ' + (el.getAttribute('data-player') || '') + ' ' + (el.getAttribute('data-download') || '')).trim()}))"""
        )
    except Exception:
        return
    ranked: list[tuple[int, int]] = []
    for row in rows or []:
        if not isinstance(row, dict): continue
        try: index = int(row.get("index"))
        except (TypeError, ValueError): continue
        href = str(row.get("href") or "")
        text = f"{row.get('text') or ''} {row.get('attr') or ''}"
        score = _navigation_score(text, href)
        if _is_download_target(text, href): score += 30
        if score >= 14: ranked.append((score, index))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    locator = page.locator(selector)
    for _, index in ranked[:max_clicks]:
        try:
            await locator.nth(index).click(timeout=1800, no_wait_after=True)
            await page.wait_for_timeout(500)
        except Exception:
            continue


async def _save_async(candidate_url: str, output_dir: str, *, validator, is_audio: bool, timeout_ms: int, settle_ms: int, max_file_bytes: int, referer_url: str | None = None) -> str | None:
    if not _is_http_url(candidate_url): return None
    try:
        validator(candidate_url)
        if referer_url: validator(referer_url)
    except Exception:
        return None
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None
    os.makedirs(output_dir, exist_ok=True)
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check"])
        except Exception:
            return None
        try:
            context = await browser.new_context(user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36", java_script_enabled=True, accept_downloads=True)
            if referer_url:
                source_page = await context.new_page()
                try:
                    await source_page.goto(referer_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    await source_page.wait_for_timeout(settle_ms)
                except Exception:
                    pass
                finally:
                    await source_page.close()
            queue = [candidate_url]
            visited: set[str] = set()
            while queue and len(visited) < DEFAULT_MAX_PAGES:
                page_url = queue.pop(0)
                if page_url in visited: continue
                visited.add(page_url)
                print(f"🌐 Browser Download Handoff: opening page {len(visited)}/{DEFAULT_MAX_PAGES}", flush=True)
                page = await context.new_page()
                media_urls: list[str] = []
                seen_media: set[str] = set()
                download_holder: list[object] = []
                def remember_media(url: str) -> None:
                    if _is_http_url(url) and url not in seen_media and len(media_urls) < DEFAULT_MAX_MEDIA_RESPONSES:
                        seen_media.add(url); media_urls.append(url)
                async def on_response(response) -> None:
                    try:
                        headers = response.headers; content_type = headers.get("content-type"); disposition = headers.get("content-disposition"); response_url = response.url
                    except Exception: return
                    if _is_media_response(response_url, content_type, disposition):
                        try: validator(response_url)
                        except Exception: return
                        remember_media(response_url)
                        print("🌐 Browser Download Handoff: captured media response", flush=True)
                async def on_download(download) -> None:
                    try: download_url = download.url
                    except Exception: return
                    if not _is_http_url(download_url): return
                    try: validator(download_url)
                    except Exception: return
                    download_holder.append(download); remember_media(download_url)
                    print("🌐 Browser Download Handoff: captured browser download", flush=True)
                page.on("response", on_response); page.on("download", on_download)
                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms, referer=referer_url)
                    await page.wait_for_timeout(settle_ms)
                    await _click_controls(page, DEFAULT_MAX_CLICKS)
                    await page.wait_for_timeout(settle_ms)
                    try:
                        dom_rows = await page.locator("video, audio, source").evaluate_all("""els => els.map(el => el.currentSrc || el.src || el.getAttribute('src') || el.getAttribute('data-src') || el.getAttribute('data-url') || '')""")
                    except Exception: dom_rows = []
                    for media_url in dom_rows or []:
                        if isinstance(media_url, str) and _is_http_url(media_url):
                            try: validator(media_url)
                            except Exception: continue
                            remember_media(media_url)

                    for download in download_holder:
                        try:
                            suggested = _safe_filename(download.suggested_filename or ("audio" if is_audio else "video"), is_audio)
                            suffix = Path(suggested).suffix
                            if suffix.lower() not in _MEDIA_FILE_EXTENSIONS: suffix = ".m4a" if is_audio else ".mp4"
                            fd, target = tempfile.mkstemp(prefix="browser_", suffix=suffix, dir=output_dir); os.close(fd)
                            try:
                                await download.save_as(target)
                                size = os.path.getsize(target)
                                if 0 < size <= max_file_bytes:
                                    print(f"🌐 Browser Download Handoff: saved {size} bytes", flush=True)
                                    return target
                                print(f"⚠️ Browser Download Handoff: saved file rejected ({size} bytes)", flush=True)
                            finally:
                                try:
                                    if os.path.exists(target) and (os.path.getsize(target) == 0 or os.path.getsize(target) > max_file_bytes): os.remove(target)
                                except OSError: pass
                        except Exception as exc:
                            print(f"⚠️ Browser Download Handoff: download.save_as failed ({type(exc).__name__})", flush=True)
                            try:
                                path = await download.path()
                                if path and os.path.isfile(path):
                                    size = os.path.getsize(path)
                                    if 0 < size <= max_file_bytes:
                                        suffix = Path(path).suffix.lower()
                                        if suffix not in _MEDIA_FILE_EXTENSIONS: suffix = ".m4a" if is_audio else ".mp4"
                                        target = tempfile.mktemp(prefix="browser_", suffix=suffix, dir=output_dir)
                                        shutil.copyfile(path, target)
                                        if os.path.getsize(target) == size:
                                            print(f"🌐 Browser Download Handoff: recovered download path ({size} bytes)", flush=True)
                                            return target
                            except Exception as path_exc:
                                print(f"⚠️ Browser Download Handoff: download path unavailable ({type(path_exc).__name__})", flush=True)

                    cookies = await context.cookies()
                    if media_urls:
                        print(f"🌐 Browser Download Handoff: streaming {len(media_urls)} captured media URL(s)", flush=True)
                    for media_url in media_urls:
                        stream_referer = referer_url or page_url
                        result = await asyncio.to_thread(_stream_with_browser_context, media_url, output_dir, cookies=cookies, referer_url=stream_referer, is_audio=is_audio, max_file_bytes=max_file_bytes, timeout_s=max(5.0, timeout_ms / 1000.0))
                        if result: return result
                    for link in await _candidate_links(page, page_url):
                        if link not in visited and link not in queue: queue.append(link)
                except Exception as exc:
                    print(f"⚠️ Browser Download Handoff: page failed ({type(exc).__name__})", flush=True)
                finally:
                    await page.close()
            await context.close()
        finally:
            await browser.close()
    return None


def resolve_to_file(candidate_url: str, output_dir: str, *, validator, is_audio: bool = False, timeout_ms: int = DEFAULT_TIMEOUT_MS, settle_ms: int = DEFAULT_SETTLE_MS, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES, referer_url: str | None = None) -> str | None:
    """Resolve a public browser candidate into a verified local file."""
    try:
        return asyncio.run(_save_async(candidate_url, output_dir, validator=validator, is_audio=is_audio, timeout_ms=timeout_ms, settle_ms=settle_ms, max_file_bytes=max_file_bytes, referer_url=referer_url))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try: return loop.run_until_complete(_save_async(candidate_url, output_dir, validator=validator, is_audio=is_audio, timeout_ms=timeout_ms, settle_ms=settle_ms, max_file_bytes=max_file_bytes, referer_url=referer_url))
        finally: loop.close()
    except Exception as exc:
        LOG.warning("Browser download handoff failed: %s", type(exc).__name__)
        return None
