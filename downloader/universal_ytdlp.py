"""Universal yt-dlp extraction fallback for the smart extraction engine.

This module is deliberately isolated from platform-specific extractors. It asks
installed yt-dlp extractors to resolve a public URL and converts the returned
formats into normalized media candidates. It does not use cookies, login
sessions, DRM bypasses, or other access-control workarounds.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import sys


DEFAULT_TIMEOUT = 45.0
DEFAULT_MAX_STDOUT = 4 * 1024 * 1024
PLUGIN_DIR = Path("/opt/yt-dlp-plugins")


@dataclass(frozen=True)
class UniversalMedia:
    url: str
    kind: str
    discovered_by: str
    confidence: int
    metadata: dict[str, Any]


def _kind_from_format(item: dict[str, Any]) -> str | None:
    protocol = str(item.get("protocol") or "").lower()
    manifest = str(item.get("manifest_url") or "").lower()
    url = str(item.get("url") or "").lower()
    ext = str(item.get("ext") or "").lower()

    if protocol in {"m3u8", "m3u8_native", "m3u8_native_hls"} or ".m3u8" in manifest or ".m3u8" in url:
        return "hls"
    if protocol in {"http_dash_segments", "dash"} or ".mpd" in manifest or ".mpd" in url:
        return "dash"
    if ext in {"mp4", "webm", "mov", "m4v", "flv", "avi"}:
        return "progressive"
    if protocol in {"http", "https"} and url.startswith(("http://", "https://")):
        return "progressive"
    return None


def _confidence(item: dict[str, Any], kind: str) -> int:
    score = {"hls": 150, "dash": 145, "progressive": 140}.get(kind, 100)
    if item.get("height"):
        score += min(int(item.get("height") or 0) // 100, 12)
    if item.get("vcodec") not in (None, "none"):
        score += 5
    if item.get("acodec") not in (None, "none"):
        score += 3
    if item.get("format_id"):
        score += 1
    return score


def _plugin_args() -> list[str]:
    if PLUGIN_DIR.is_dir():
        return ["--plugin-dirs", str(PLUGIN_DIR)]
    return []


def extract_with_yt_dlp(
    source_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_stdout: int = DEFAULT_MAX_STDOUT,
) -> list[UniversalMedia]:
    """Resolve a public URL through the installed yt-dlp extractor registry."""
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("source_url must be a non-empty string")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    env = os.environ.copy()
    if PLUGIN_DIR.is_dir():
        env["YTDLP_PLUGIN_DIRS"] = str(PLUGIN_DIR)

    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        *_plugin_args(),
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--no-playlist",
        source_url.strip(),
    ]

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )

    stdout = completed.stdout or ""
    if len(stdout) > max_stdout:
        stdout = stdout[:max_stdout]

    if completed.returncode != 0 and not stdout.strip():
        raise RuntimeError("yt-dlp did not return extraction metadata")

    try:
        info = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned invalid JSON metadata") from exc

    formats = info.get("formats") or []
    if not isinstance(formats, list):
        formats = []

    results: list[UniversalMedia] = []
    seen: set[tuple[str, str]] = set()

    for item in formats:
        if not isinstance(item, dict):
            continue
        media_url = item.get("url")
        if not isinstance(media_url, str) or not media_url.startswith(("http://", "https://")):
            continue

        kind = _kind_from_format(item)
        if kind is None:
            continue

        key = (media_url, kind)
        if key in seen:
            continue
        seen.add(key)

        results.append(
            UniversalMedia(
                url=media_url,
                kind=kind,
                discovered_by="yt_dlp_universal",
                confidence=_confidence(item, kind),
                metadata={
                    "format_id": item.get("format_id"),
                    "ext": item.get("ext"),
                    "protocol": item.get("protocol"),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "fps": item.get("fps"),
                    "vcodec": item.get("vcodec"),
                    "acodec": item.get("acodec"),
                    "filesize": item.get("filesize") or item.get("filesize_approx"),
                    "extractor": info.get("extractor_key") or info.get("extractor"),
                },
            )
        )

    direct_url = info.get("url")
    if isinstance(direct_url, str) and direct_url.startswith(("http://", "https://")):
        kind = _kind_from_format(info) or "progressive"
        key = (direct_url, kind)
        if key not in seen:
            results.append(
                UniversalMedia(
                    url=direct_url,
                    kind=kind,
                    discovered_by="yt_dlp_universal",
                    confidence=145,
                    metadata={"extractor": info.get("extractor_key") or info.get("extractor")},
                )
            )

    results.sort(key=lambda item: (-item.confidence, item.url))
    return results
