"""Direct yt-dlp fallback for public Threads URLs.

This is intentionally narrow: it only accepts threads.com / threads.net URLs,
invokes the installed yt-dlp CLI with the Threads extractor plugin, and returns
only media URLs exposed by yt-dlp. No login, cookies, or access-control bypass
is used.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from urllib.parse import urlparse

from .threads_extractor import ThreadsMedia

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 45
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_SHARE_PATH = re.compile(r"/share/[A-Za-z0-9_-]{3,128}(?:/|$)", re.IGNORECASE)


def _is_threads_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}


def _kind(media_url: str, format_data: dict) -> str:
    protocol = str(format_data.get("protocol") or "").lower()
    ext = str(format_data.get("ext") or "").lower()
    path = urlparse(media_url).path.lower()
    if protocol in {"m3u8", "m3u8_native"} or path.endswith(".m3u8"):
        return "hls"
    if protocol == "http_dash_segments" or path.endswith(".mpd"):
        return "dash"
    if ext in {"mp4", "webm", "mov"} or path.endswith((".mp4", ".webm", ".mov")):
        return "progressive"
    return "progressive"


def _media_from_info(info: object, source_url: str) -> list[ThreadsMedia]:
    result: list[ThreadsMedia] = []
    seen: set[tuple[str, str]] = set()

    def add(url: object, data: dict | None = None, confidence: int = 150) -> None:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return
        data = data if isinstance(data, dict) else {}
        kind = _kind(url, data)
        key = (url, kind)
        if key in seen:
            return
        seen.add(key)
        result.append(ThreadsMedia(url, kind, "yt_dlp_threads", confidence))

    if not isinstance(info, dict):
        return result

    formats = info.get("formats")
    if isinstance(formats, list):
        for item in formats:
            if not isinstance(item, dict):
                continue
            add(item.get("url"), item, 152)

    add(info.get("url"), info, 155)

    # Some extractors expose nested entries for carousel/reposted media.
    entries = info.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            result.extend(_media_from_info(entry, source_url))

    result.sort(key=lambda item: (-item.confidence, item.kind, item.url))
    return result[:50]


def extract_threads_with_yt_dlp(url: str, *, timeout: int = _TIMEOUT_SECONDS) -> list[ThreadsMedia]:
    """Ask the installed yt-dlp Threads plugin for publicly exposed media."""
    if not _is_threads_url(url):
        return []

    env = os.environ.copy()
    env["YTDLP_PLUGIN_DIRS"] = env.get("YTDLP_PLUGIN_DIRS", "/opt/yt-dlp-plugins")

    command = [
        "python", "-m", "yt_dlp",
        "--plugin-dirs", env["YTDLP_PLUGIN_DIRS"],
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--no-playlist",
        url,
    ]

    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=env,
            text=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("Threads yt-dlp fallback timed out")
        return []
    except (OSError, ValueError) as exc:
        log.warning("Threads yt-dlp fallback could not start: %s", type(exc).__name__)
        return []

    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    if len(stdout) > _MAX_OUTPUT_BYTES:
        log.warning("Threads yt-dlp fallback output exceeded size limit")
        return []

    if completed.returncode != 0:
        safe_error = stderr.decode("utf-8", errors="replace")[:500]
        log.warning("Threads yt-dlp fallback failed rc=%s: %s", completed.returncode, safe_error)
        return []

    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        log.warning("Threads yt-dlp fallback returned invalid JSON")
        return []

    result = _media_from_info(payload, url)
    log.info("Threads yt-dlp fallback: candidates=%d", len(result))
    return result
