"""Bounded browser handoff for public Shhaiid4u provider candidates.

This layer is deliberately isolated to Shhaiid4u -> Megaup/Streamtape. It
reuses the existing browser download handoff so provider pages can establish
public playback requests with the Shhaiid4u referrer and browser context.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

SUPPORTED_HOSTS = {"megaup.net", "streamtape.com"}
MAX_CANDIDATES = 6
TIMEOUT_MS = 45_000
SETTLE_MS = 2_000
MIN_VIDEO_BYTES = 2 * 1024 * 1024
MIN_VIDEO_DURATION = 45.0


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().rstrip(".")
    except Exception:
        return ""


def is_supported_candidate(url: str) -> bool:
    host = _hostname(url)
    return host in SUPPORTED_HOSTS or any(host.endswith("." + item) for item in SUPPORTED_HOSTS)


def _local_file(path: object, temp_dir: str) -> bool:
    if not isinstance(path, (str, os.PathLike)) or not temp_dir:
        return False
    try:
        real = os.path.realpath(os.fspath(path))
        root = os.path.realpath(temp_dir) + os.sep
        return real.startswith(root) and os.path.isfile(real) and os.path.getsize(real) > 0
    except OSError:
        return False


def _owned_candidates(candidates: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            continue
        if not is_supported_candidate(value) or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= MAX_CANDIDATES:
            break
    return result


async def _handoff(candidates: list[str], *, source_url: str, temp_dir: str, is_audio: bool, max_file_bytes: int, validator) -> tuple[str | None, dict]:
    try:
        from downloader.browser_download_handoff import resolve_to_file
    except Exception:
        return None, {"status": "unavailable"}

    owned = _owned_candidates(candidates)
    diagnostics = {"status": "failed", "candidate_count": len(owned), "providers": []}
    for candidate in owned:
        provider = _hostname(candidate)
        entry = {"provider": provider, "candidate": candidate, "status": "failed"}
        diagnostics["providers"].append(entry)
        try:
            path = await asyncio.to_thread(
                resolve_to_file,
                candidate,
                temp_dir,
                timeout_ms=TIMEOUT_MS,
                settle_ms=SETTLE_MS,
                validator=validator,
                is_audio=is_audio,
                max_file_bytes=max_file_bytes,
                referer_url=source_url,
                min_video_bytes=MIN_VIDEO_BYTES if not is_audio else 0,
                min_video_duration=MIN_VIDEO_DURATION if not is_audio else 0.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            entry["error"] = type(exc).__name__
            continue
        if _local_file(path, temp_dir):
            entry["status"] = "success"
            diagnostics["status"] = "success"
            return os.fspath(path), diagnostics
    return None, diagnostics


def install(bot_module) -> None:
    """Wrap only Shhaiid4u fallback downloads; preserve every other platform."""
    original = getattr(bot_module, "download_with_fallback", None)
    if not callable(original) or getattr(original, "_shhaiid4u_provider_browser_handoff", False):
        return

    async def wrapped(url, temp_dir, output_template, format_option, is_audio=False, attempt_id=None, attempt_number=None):
        result = await original(
            url, temp_dir, output_template, format_option,
            is_audio=is_audio, attempt_id=attempt_id, attempt_number=attempt_number,
        )
        if isinstance(result, tuple) and result and _local_file(result[0], temp_dir):
            return result
        if not isinstance(url, str) or _hostname(url) != "shhaiid4u.net":
            return result
        try:
            from downloader.shhaiid4u_player_bridge import resolve
            candidates = await asyncio.to_thread(resolve, url, validator=bot_module.validate_public_http_url)
            if not candidates:
                return result
            max_bytes = getattr(bot_module, "MAX_AUDIO_DOWNLOAD_BYTES" if is_audio else "MAX_VIDEO_DOWNLOAD_BYTES", 500 * 1024 * 1024)
            path, diagnostics = await _handoff(
                candidates,
                source_url=url,
                temp_dir=temp_dir,
                is_audio=is_audio,
                max_file_bytes=max_bytes,
                validator=bot_module.validate_public_http_url,
            )
            if path:
                print(f"🎯 Shhaiid4u Provider Browser Handoff: succeeded via {diagnostics['providers'][-1]['provider']}", flush=True)
                return path, "Shhaiid4u Provider Browser Handoff: saved local file", "", {
                    "attempt_id": attempt_id,
                    "attempt_number": attempt_number,
                    "provider_handoff": diagnostics,
                }
            print("🎯 Shhaiid4u Provider Browser Handoff: no verified provider file", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ Shhaiid4u Provider Browser Handoff: failed ({type(exc).__name__})", flush=True)
        return result

    wrapped._shhaiid4u_provider_browser_handoff = True
    bot_module.download_with_fallback = wrapped
    print("🎯 Shhaiid4u Provider Browser Handoff: ENABLED", flush=True)
