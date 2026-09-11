"""Conservative post-download gate for public movie/player pages.

This layer never bypasses authentication, DRM, CAPTCHA, or access controls.
It only rejects clearly implausible media artifacts (for example, a tiny
advertisement clip returned instead of a movie) before delivery.
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

_SOCIAL_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "tiktok.com",
    "instagram.com", "facebook.com", "fb.watch", "twitter.com", "x.com",
    "vimeo.com", "dailymotion.com", "reddit.com", "soundcloud.com",
)
_MOVIE_HOST_HINTS = (
    "shahid4u", "mycima", "wecima", "egybest", "faselhd", "arabseed",
    "akwam", "cima", "film", "movie", "series", "episode",
)
_MOVIE_PATH_HINTS = (
    "movie", "film", "watch", "player", "episode", "series", "embed",
    "مسلسل", "فيلم", "مشاهدة", "حلقة", "حلقه", "سيرفر",
)
_AD_HOST_HINTS = (
    "doubleclick", "googlesyndication", "googleadservices", "adservice",
    "adsystem", "advertising", "adserver", "popads", "propellerads",
)


@dataclass(frozen=True)
class MediaGateResult:
    accepted: bool
    guarded: bool
    reason: str
    size_bytes: int = 0
    duration_seconds: float | None = None


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _contains_hint(value: str, hints: tuple[str, ...]) -> bool:
    lowered = value.casefold()
    return any(hint.casefold() in lowered for hint in hints)


def should_guard(source_url: str, *, is_audio: bool = False) -> bool:
    """Return True only for likely public movie/player pages."""
    if is_audio or not isinstance(source_url, str):
        return False
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = _host(source_url)
    if any(host == item or host.endswith("." + item) for item in _SOCIAL_HOSTS):
        return False
    return (
        _contains_hint(host, _MOVIE_HOST_HINTS)
        or _contains_hint(parsed.path, _MOVIE_PATH_HINTS)
        or _contains_hint(parsed.query, _MOVIE_PATH_HINTS)
    )


def is_ad_host(media_url: str | None) -> bool:
    """Reject obvious advertising/tracking media endpoints."""
    if not media_url:
        return False
    return _contains_hint(_host(media_url), _AD_HOST_HINTS)


def _probe(path: str) -> tuple[float | None, bool]:
    """Read only local metadata; never decodes or copies the media."""
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-show_entries", "stream=codec_type",
                "-of", "json", path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, False
    if completed.returncode != 0:
        return None, False
    try:
        payload = json.loads(completed.stdout or "{}")
        duration_raw = (payload.get("format") or {}).get("duration")
        duration = float(duration_raw) if duration_raw is not None else None
        streams = payload.get("streams") or []
        has_video = any(
            isinstance(item, dict) and item.get("codec_type") == "video"
            for item in streams
        )
        return duration, has_video
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, False


def assess_local_media(
    path: str,
    source_url: str,
    *,
    candidate_url: str | None = None,
    is_audio: bool = False,
    min_bytes: int = 2 * 1024 * 1024,
    min_duration: float = 45.0,
) -> MediaGateResult:
    """Validate a completed local video only when the source looks movie-like."""
    if not should_guard(source_url, is_audio=is_audio):
        return MediaGateResult(True, False, "not_movie_source")
    if not os.path.isfile(path):
        return MediaGateResult(False, True, "missing_file")
    try:
        size = os.path.getsize(path)
    except OSError:
        return MediaGateResult(False, True, "stat_failed")
    if size <= 0:
        return MediaGateResult(False, True, "empty_file", size)
    if candidate_url and is_ad_host(candidate_url):
        return MediaGateResult(False, True, "advertising_host", size)
    if size < min_bytes:
        return MediaGateResult(False, True, "implausibly_small_media", size)
    duration, has_video = _probe(path)
    if not has_video:
        return MediaGateResult(False, True, "no_video_stream", size, duration)
    if duration is None:
        return MediaGateResult(False, True, "duration_unavailable", size)
    if duration < min_duration:
        return MediaGateResult(False, True, "implausibly_short_media", size, duration)
    return MediaGateResult(True, True, "accepted", size, duration)


def _local_file(value: object, temp_dir: object) -> bool:
    if not isinstance(value, (str, os.PathLike)) or not temp_dir:
        return False
    try:
        path = os.path.realpath(os.fspath(value))
        root = os.path.realpath(os.fspath(temp_dir)) + os.sep
        return path.startswith(root) and os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _source_url(args: tuple[object, ...], kwargs: dict[str, object]) -> str | None:
    value = kwargs.get("url")
    if value is None and args:
        value = args[0]
    return value if isinstance(value, str) else None


def _reject_result(result: object, path: str, reason: str) -> object:
    try:
        os.remove(path)
    except OSError:
        pass
    print(f"🛡️ Movie Source Guard: rejected media ({reason})", flush=True)
    if isinstance(result, tuple):
        values = list(result)
        if values:
            values[0] = None
        if len(values) > 1 and isinstance(values[1], str):
            values[1] = f"Movie Source Guard: rejected ({reason})"
        return tuple(values)
    return None


def _wrap_async_download(original, label: str):
    async def guarded(*args, **kwargs):
        result = original(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        source_url = _source_url(args, kwargs)
        temp_dir = kwargs.get("temp_dir")
        is_audio = bool(kwargs.get("is_audio", False))
        if isinstance(result, tuple) and result and _local_file(result[0], temp_dir) and source_url:
            candidate_url = None
            diagnostics = result[3] if len(result) > 3 else {}
            if isinstance(diagnostics, dict):
                candidate_url = diagnostics.get("candidate_url") or diagnostics.get("handoff_candidate")
            gate = assess_local_media(
                result[0],
                source_url,
                candidate_url=candidate_url,
                is_audio=is_audio,
            )
            if not gate.accepted:
                return _reject_result(result, result[0], gate.reason)
            if gate.guarded:
                print(
                    f"🛡️ Movie Source Guard: accepted verified {label} media "
                    f"({gate.duration_seconds:.1f}s, {gate.size_bytes} bytes)",
                    flush=True,
                )
        return result

    guarded._movie_source_guard = True
    return guarded


def install(bot_module) -> None:
    """Install the guard before the existing Smart Media Bridge is composed."""
    original_fallback = getattr(bot_module, "download_with_fallback", None)
    original_smart = getattr(bot_module, "download_with_smart_extraction", None)

    if callable(original_fallback) and not getattr(original_fallback, "_movie_source_guard", False):
        bot_module.download_with_fallback = _wrap_async_download(original_fallback, "fallback")

    if callable(original_smart) and not getattr(original_smart, "_movie_source_guard", False):
        bot_module.download_with_smart_extraction = _wrap_async_download(original_smart, "smart")

    print("🛡️ Movie Source Guard: ENABLED", flush=True)
