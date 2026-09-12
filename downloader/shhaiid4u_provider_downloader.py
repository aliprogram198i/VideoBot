"""Bounded provider download handoff for public Shhaiid4u candidates.

This layer is isolated from the generic downloader. It owns only public
MegaUp and Streamtape candidates discovered from Shhaiid4u pages. It first
tries yt-dlp with the source referrer, then uses the existing public browser
stack to observe the provider's actual media response. Concrete media URLs
are downloaded directly with the provider Referer before yt-dlp is retried.

No accounts, cookies, CAPTCHA solving, DRM bypass, or access-control
workarounds are used.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
from urllib.parse import urlparse

import httpx

MAX_PROVIDER_CANDIDATES = 6
MAX_PROVIDER_PAGES = 4
MAX_MEDIA_RESPONSES = 40
PROVIDER_TIMEOUT_S = 75
BROWSER_TIMEOUT_MS = 30_000
SETTLE_MS = 2_000
MAX_FILE_BYTES = 500 * 1024 * 1024
MIN_VIDEO_BYTES = 2 * 1024 * 1024
MIN_VIDEO_DURATION = 45.0
MEDIA_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mov", ".mkv", ".m3u8", ".mpd", ".ts")
MEDIA_CONTENT_HINTS = ("video/", "audio/", "mpegurl", "dash+xml")
DIRECT_MEDIA_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mov", ".mkv")
SUPPORTED_HOSTS = {"megaup.net", "streamtape.com"}
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36"
)


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().rstrip(".")
    except Exception:
        return ""


def is_supported_candidate(url: str) -> bool:
    host = _hostname(url)
    return host in SUPPORTED_HOSTS or any(host.endswith("." + item) for item in SUPPORTED_HOSTS)


def _is_http(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except Exception:
        return False


def _looks_like_media(url: str, content_type: str | None = None) -> bool:
    path = (urlparse(url).path or "").casefold()
    value = f"{url} {content_type or ''}".casefold()
    return any(path.endswith(ext) for ext in MEDIA_EXTENSIONS) or any(
        hint in value for hint in MEDIA_CONTENT_HINTS
    )


def _looks_like_direct_media(url: str, content_type: str | None = None) -> bool:
    path = (urlparse(url).path or "").casefold()
    value = f"{url} {content_type or ''}".casefold()
    return any(path.endswith(ext) for ext in DIRECT_MEDIA_EXTENSIONS) or "video/" in value or "audio/" in value


def _provider_candidates(candidates: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str) or not _is_http(candidate):
            continue
        if not is_supported_candidate(candidate) or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
        if len(result) >= MAX_PROVIDER_CANDIDATES:
            break
    return result


def _safe_output_path(temp_dir: str, stdout_text: str, extensions: tuple[str, ...]) -> str | None:
    root = os.path.realpath(temp_dir) + os.sep
    for line in reversed((stdout_text or "").splitlines()):
        value = line.strip()
        if not value:
            continue
        real = os.path.realpath(value)
        if real.startswith(root) and os.path.isfile(real) and real.casefold().endswith(extensions):
            return real
    try:
        paths = []
        for name in os.listdir(temp_dir):
            path = os.path.realpath(os.path.join(temp_dir, name))
            if path.startswith(root) and os.path.isfile(path) and path.casefold().endswith(extensions):
                paths.append(path)
        return max(paths, key=os.path.getmtime) if paths else None
    except OSError:
        return None


def _verify_file(path: str, *, is_audio: bool) -> bool:
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size <= 0 or size > MAX_FILE_BYTES:
        return False
    if is_audio:
        return True
    if size < MIN_VIDEO_BYTES:
        return False
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if probe.returncode != 0:
            return False
        return float((probe.stdout or "").strip()) >= MIN_VIDEO_DURATION
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _short_error(text: str | None) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[-600:] if value else "no provider error text"


def _extract_streamtape_direct_urls(html: str, page_url: str) -> list[str]:
    if _hostname(page_url) != "streamtape.com" or not isinstance(html, str):
        return []
    tokens = re.findall(r"token=([^&'\"\s]+)", html, flags=re.IGNORECASE)
    links = re.findall(r"id=[\"']ideoooolink[\"'][^>]*>([^<]+)<", html, flags=re.IGNORECASE)
    if not tokens or not links:
        return []
    token = tokens[-1]
    results: list[str] = []
    for raw in reversed(links):
        value = raw.strip().replace("\\/", "/")
        if value.startswith("//"):
            value = "https:" + value
        elif value.startswith("https:/") and not value.startswith("https://"):
            value = "https://" + value[len("https:/"):].lstrip("/")
        elif value.startswith("http:/") and not value.startswith("http://"):
            value = "http://" + value[len("http:/"):].lstrip("/")
        if not _is_http(value):
            continue
        direct = f"{value}{'&' if '?' in value else '?'}token={token}&dl=1"
        if direct not in results:
            results.append(direct)
    return results[:4]


async def _run_ytdlp(candidate_url: str, *, temp_dir: str, referer_url: str,
                     is_audio: bool, max_size: int) -> tuple[str | None, str, str]:
    extensions = (".mp3", ".m4a", ".opus", ".aac", ".wav") if is_audio else (".mp4", ".mkv", ".webm", ".mov")
    command = [
        "python", "-m", "yt_dlp", "--no-playlist", "--retries", "5",
        "--fragment-retries", "5", "--socket-timeout", "45",
        "--max-filesize", str(max_size), "--referer", referer_url,
        "--user-agent", USER_AGENT, "--print", "after_move:filepath",
        "--no-warnings", "-o", os.path.join(temp_dir, "shhaiid4u_provider_%(id)s.%(ext)s"),
    ]
    if is_audio:
        command.extend(["-x", "--audio-format", "mp3"])
    else:
        command.extend(["--merge-output-format", "mp4"])
    command.append(candidate_url)
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=PROVIDER_TIMEOUT_S)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            return None, "", "provider yt-dlp timeout"
        stdout_text = stdout.decode(errors="ignore")
        stderr_text = stderr.decode(errors="ignore")
        if process.returncode != 0:
            return None, stdout_text, stderr_text
        path = _safe_output_path(temp_dir, stdout_text, extensions)
        if path and _verify_file(path, is_audio=is_audio):
            return path, stdout_text, stderr_text
        return None, stdout_text, "provider yt-dlp returned no verified media file"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return None, "", f"provider yt-dlp error: {type(exc).__name__}"


async def _download_direct_media(media_url: str, *, temp_dir: str, referer_url: str,
                                 is_audio: bool, max_size: int, media_index: int) -> tuple[str | None, str]:
    """Download a concrete public media URL without asking yt-dlp to identify it."""
    if not _is_http(media_url):
        return None, "invalid media URL"
    if not _looks_like_direct_media(media_url):
        return None, "not a direct media URL"
    suffix = ".mp3" if is_audio else (os.path.splitext(urlparse(media_url).path)[1].casefold() or ".mp4")
    if suffix not in ((".mp3",) if is_audio else DIRECT_MEDIA_EXTENSIONS):
        suffix = ".mp4"
    path = os.path.join(temp_dir, f"shhaiid4u_direct_{media_index}{suffix}")
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer_url,
        "Accept": "video/*,audio/*,*/*;q=0.8",
        "Accept-Encoding": "identity",
    }
    timeout = httpx.Timeout(PROVIDER_TIMEOUT_S, connect=15.0)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
            async with client.stream("GET", media_url) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    return None, f"HTTP {response.status_code}"
                content_type = (response.headers.get("content-type") or "").casefold()
                if not _looks_like_direct_media(media_url, content_type):
                    return None, f"unexpected content-type={content_type or 'unknown'}"
                try:
                    declared = int(response.headers.get("content-length") or "0")
                except ValueError:
                    declared = 0
                if declared and declared > max_size:
                    return None, "content exceeds configured max size"
                written = 0
                with open(path, "wb") as output:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        written += len(chunk)
                        if written > max_size or written > MAX_FILE_BYTES:
                            output.close()
                            try:
                                os.remove(path)
                            except OSError:
                                pass
                            return None, "download exceeded maximum size"
                        output.write(chunk)
                if _verify_file(path, is_audio=is_audio):
                    return path, ""
                try:
                    os.remove(path)
                except OSError:
                    pass
                return None, "downloaded file failed media verification"
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        try:
            os.remove(path)
        except OSError:
            pass
        return None, f"direct HTTP error={type(exc).__name__}"


async def _discover_browser_media(candidate_url: str, *, referer_url: str) -> list[str]:
    """Observe public media responses from the provider page for both owned hosts."""
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        print(f"⚠️ Shhaiid4u Provider Downloader: Playwright unavailable ({type(exc).__name__})", flush=True)
        return []

    media_urls: list[str] = []
    seen: set[str] = set()

    def remember(value: str) -> None:
        if not isinstance(value, str) or not _is_http(value) or value in seen:
            return
        if len(media_urls) >= MAX_MEDIA_RESPONSES:
            return
        seen.add(value)
        media_urls.append(value)

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                      "--no-first-run", "--no-default-browser-check"],
            )
        except Exception as exc:
            print(f"⚠️ Shhaiid4u Provider Downloader: Chromium launch failed ({type(exc).__name__})", flush=True)
            return []
        try:
            context = await browser.new_context(user_agent=USER_AGENT, java_script_enabled=True, accept_downloads=True)
            source_page = await context.new_page()
            try:
                await source_page.goto(referer_url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
                await source_page.wait_for_timeout(SETTLE_MS)
            except Exception:
                pass
            finally:
                await source_page.close()

            queue = [candidate_url]
            visited: set[str] = set()
            while queue and len(visited) < MAX_PROVIDER_PAGES and not media_urls:
                page_url = queue.pop(0)
                if page_url in visited:
                    continue
                visited.add(page_url)
                page = await context.new_page()

                async def on_response(response) -> None:
                    try:
                        response_url = response.url
                        content_type = (response.headers.get("content-type") or "")
                    except Exception:
                        return
                    if _looks_like_media(response_url, content_type):
                        remember(response_url)

                page.on("response", on_response)
                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS, referer=referer_url)
                    await page.wait_for_timeout(SETTLE_MS)
                    selector = "a[href], button, [role='button'], video, source, iframe[src], embed[src]"
                    try:
                        rows = await page.locator(selector).evaluate_all(
                            """els => els.map(el => ({url: el.currentSrc || el.src || el.href || '', text: (el.innerText || el.textContent || '').trim()}))"""
                        )
                    except Exception:
                        rows = []
                    for row in rows or []:
                        if not isinstance(row, dict):
                            continue
                        value = str(row.get("url") or "")
                        if _is_http(value) and _looks_like_media(value):
                            remember(value)
                        elif _is_http(value) and any(x in value.casefold() for x in ("stream", "source", "player", "download")):
                            if value not in visited and value not in queue and len(queue) < MAX_PROVIDER_PAGES:
                                queue.append(value)
                    try:
                        html = await page.content()
                    except Exception:
                        html = ""
                    if _hostname(page_url) == "streamtape.com":
                        for direct in _extract_streamtape_direct_urls(html, page_url):
                            remember(direct)
                    for value in re.findall(r"https?://[^\"'<>\s]+", html):
                        value = value.rstrip(".,);]}")
                        if _is_http(value) and _looks_like_media(value):
                            remember(value)
                except Exception as exc:
                    print(f"⚠️ Shhaiid4u Provider Downloader: browser page failed ({type(exc).__name__})", flush=True)
                finally:
                    await page.close()
            await context.close()
        finally:
            await browser.close()
    return media_urls[:MAX_MEDIA_RESPONSES]


async def download_candidates(candidates: list[str], *, source_url: str, temp_dir: str,
                              is_audio: bool, max_size: int) -> tuple[str | None, dict]:
    """Try owned provider URLs with bounded direct-media and yt-dlp fallbacks."""
    if not isinstance(source_url, str) or not _is_http(source_url):
        return None, {"status": "skipped", "reason": "invalid_source"}
    owned = _provider_candidates(candidates)
    if not owned:
        return None, {"status": "skipped", "reason": "no_owned_candidates"}

    diagnostics = {"status": "failed", "providers": [], "candidate_count": len(owned)}
    for index, candidate in enumerate(owned, 1):
        provider = _hostname(candidate)
        entry = {"provider": provider, "candidate": candidate, "status": "failed"}
        diagnostics["providers"].append(entry)
        print(f"🎯 Shhaiid4u Provider Downloader: candidate {index}/{len(owned)} provider={provider}", flush=True)

        path, _, stderr = await _run_ytdlp(
            candidate, temp_dir=temp_dir, referer_url=source_url,
            is_audio=is_audio, max_size=max_size,
        )
        if path:
            entry["status"] = "success"
            diagnostics["status"] = "success"
            print(f"✅ Shhaiid4u Provider Downloader: yt-dlp succeeded provider={provider}", flush=True)
            return path, diagnostics
        entry["initial_error"] = _short_error(stderr)
        print(f"⚠️ Shhaiid4u Provider Downloader: yt-dlp failed provider={provider}: {_short_error(stderr)}", flush=True)

        media_urls = await _discover_browser_media(candidate, referer_url=source_url)
        entry["browser_media_count"] = len(media_urls)
        print(f"🔎 Shhaiid4u Provider Downloader: browser media candidates={len(media_urls)} provider={provider}", flush=True)
        for media_index, media_url in enumerate(media_urls, 1):
            direct_path, direct_error = await _download_direct_media(
                media_url, temp_dir=temp_dir, referer_url=candidate,
                is_audio=is_audio, max_size=max_size, media_index=media_index,
            )
            if direct_path:
                entry["status"] = "success"
                entry["status_detail"] = "direct_http_media"
                diagnostics["status"] = "success"
                print(f"✅ Shhaiid4u Provider Downloader: direct media succeeded provider={provider} media={media_index}", flush=True)
                return direct_path, diagnostics
            if direct_error == "not a direct media URL":
                path, _, media_stderr = await _run_ytdlp(
                    media_url, temp_dir=temp_dir, referer_url=candidate,
                    is_audio=is_audio, max_size=max_size,
                )
                if path:
                    entry["status"] = "success"
                    entry["status_detail"] = "browser_media_url"
                    diagnostics["status"] = "success"
                    print(f"✅ Shhaiid4u Provider Downloader: browser media succeeded provider={provider} media={media_index}", flush=True)
                    return path, diagnostics
                entry["media_error"] = _short_error(media_stderr)
            else:
                entry["direct_media_error"] = _short_error(direct_error)
        if media_urls:
            print(f"⚠️ Shhaiid4u Provider Downloader: browser media URLs exhausted provider={provider}", flush=True)

    return None, diagnostics


def install(bot_module) -> None:
    """Wrap only Shhaiid4u downloads; all other platforms remain untouched."""
    original = getattr(bot_module, "download_with_fallback", None)
    if not callable(original) or getattr(original, "_shhaiid4u_provider_downloader", False):
        return

    async def wrapped(url, temp_dir, output_template, format_option, is_audio=False, attempt_id=None, attempt_number=None):
        if isinstance(url, str) and _hostname(url) == "shhaiid4u.net":
            try:
                from downloader.shhaiid4u_player_bridge import resolve
                candidates = await asyncio.to_thread(resolve, url, validator=bot_module.validate_public_http_url)
                if candidates:
                    path, diagnostics = await download_candidates(
                        candidates, source_url=url, temp_dir=temp_dir, is_audio=is_audio,
                        max_size=(bot_module.MAX_AUDIO_DOWNLOAD_BYTES if is_audio else bot_module.MAX_VIDEO_DOWNLOAD_BYTES),
                    )
                    if path:
                        return path, "", "", {
                            "attempt_id": attempt_id, "attempt_number": attempt_number,
                            "candidate_count": len(candidates),
                            "candidates": diagnostics.get("providers", []),
                            "provider_handoff": diagnostics, "total_duration_ms": 0,
                        }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ Shhaiid4u Provider Downloader: failed ({type(exc).__name__})", flush=True)
        return await original(
            url, temp_dir, output_template, format_option,
            is_audio=is_audio, attempt_id=attempt_id, attempt_number=attempt_number,
        )

    wrapped._shhaiid4u_provider_downloader = True
    bot_module.download_with_fallback = wrapped
    print("🎯 Shhaiid4u Provider Downloader: ENABLED", flush=True)
