"""Canonical Shhaiid4u provider router/downloader.

This layer is isolated to Shhaiid4u. It delegates provider-specific browser
work to explicit adapters and keeps the existing generic downloader as the
final fallback. Other platforms and the database are untouched.

No authentication bypass, CAPTCHA solving, DRM bypass, or access-control
workarounds are implemented.
"""
from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import urlparse

from .shhaiid4u_provider_adapters import adapter_for

MAX_PROVIDER_CANDIDATES = 6


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().rstrip(".")
    except Exception:
        return ""


def _is_http(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except Exception:
        return False


def _owned_candidates(candidates: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not _is_http(candidate):
            continue
        if candidate in seen or adapter_for(candidate) is None:
            continue
        seen.add(candidate)
        result.append(candidate)
        if len(result) >= MAX_PROVIDER_CANDIDATES:
            break
    return result


def _short_error(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[-600:] if text else "no error text"


async def _try_ytdlp(
    candidate: str,
    *,
    temp_dir: str,
    source_url: str,
    is_audio: bool,
    max_size: int,
) -> str | None:
    extensions = (
        (".mp3", ".m4a", ".opus", ".aac", ".wav")
        if is_audio
        else (".mp4", ".mkv", ".webm", ".mov")
    )
    output = os.path.join(temp_dir, "shhaiid4u_provider_%(id)s.%(ext)s")
    command = [
        "python",
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--socket-timeout",
        "30",
        "--max-filesize",
        str(max_size),
        "--referer",
        source_url,
        "--user-agent",
        (
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36"
        ),
        "--print",
        "after_move:filepath",
        "--no-warnings",
        "-o",
        output,
    ]
    if is_audio:
        command.extend(["-x", "--audio-format", "mp3"])
    else:
        command.extend(["--merge-output-format", "mp4"])
    command.append(candidate)

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=75)
    except asyncio.TimeoutError:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        return None
    except asyncio.CancelledError:
        raise
    except Exception:
        return None

    if process.returncode != 0:
        print(
            f"⚠️ Shhaiid4u Provider Adapter: yt-dlp failed "
            f"candidate={_hostname(candidate)} error={_short_error(stderr.decode(errors='ignore'))}",
            flush=True,
        )
        return None

    root = os.path.realpath(temp_dir) + os.sep
    for line in reversed(stdout.decode(errors="ignore").splitlines()):
        path = line.strip()
        if (
            path
            and os.path.isfile(path)
            and os.path.realpath(path).startswith(root)
            and path.casefold().endswith(extensions)
        ):
            return path
    return None


async def _download_with_adapters(
    candidates: list[str],
    *,
    source_url: str,
    temp_dir: str,
    is_audio: bool,
    max_size: int,
    validator,
) -> tuple[str | None, dict]:
    owned = _owned_candidates(candidates)
    diagnostics = {
        "status": "failed",
        "candidate_count": len(owned),
        "providers": [],
    }
    if not owned:
        diagnostics["reason"] = "no_owned_candidates"
        return None, diagnostics

    for index, candidate in enumerate(owned, 1):
        adapter = adapter_for(candidate)
        if adapter is None:
            continue
        provider = adapter.hostname
        normalized = adapter.normalize(candidate)
        entry = {
            "provider": provider,
            "candidate": normalized,
            "status": "failed",
        }
        diagnostics["providers"].append(entry)
        print(
            f"🎯 Shhaiid4u Provider Adapter: candidate {index}/{len(owned)} "
            f"provider={provider}",
            flush=True,
        )

        path = await _try_ytdlp(
            normalized,
            temp_dir=temp_dir,
            source_url=source_url,
            is_audio=is_audio,
            max_size=max_size,
        )
        if path:
            entry["status"] = "success"
            entry["status_detail"] = "yt_dlp"
            diagnostics["status"] = "success"
            print(
                f"✅ Shhaiid4u Provider Adapter: yt-dlp succeeded "
                f"provider={provider}",
                flush=True,
            )
            return path, diagnostics

        print(
            f"🔎 Shhaiid4u Provider Adapter: browser handoff "
            f"provider={provider} settle_ms={adapter.settle_ms}",
            flush=True,
        )
        try:
            path = await asyncio.to_thread(
                adapter.resolve,
                normalized,
                output_dir=temp_dir,
                validator=validator,
                is_audio=is_audio,
                max_file_bytes=max_size,
                source_url=source_url,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            entry["browser_error"] = type(exc).__name__
            print(
                f"⚠️ Shhaiid4u Provider Adapter: browser handoff failed "
                f"provider={provider} error={type(exc).__name__}",
                flush=True,
            )
            path = None

        if path:
            entry["status"] = "success"
            entry["status_detail"] = "browser_download_handoff"
            diagnostics["status"] = "success"
            print(
                f"✅ Shhaiid4u Provider Adapter: verified file succeeded "
                f"provider={provider}",
                flush=True,
            )
            return path, diagnostics

    return None, diagnostics


def install(bot_module) -> None:
    """Wrap only Shhaiid4u downloads and preserve the generic fallback."""
    original = getattr(bot_module, "download_with_fallback", None)
    if not callable(original) or getattr(
        original, "_shhaiid4u_provider_downloader", False
    ):
        return

    async def wrapped(
        url,
        temp_dir,
        output_template,
        format_option,
        is_audio=False,
        attempt_id=None,
        attempt_number=None,
    ):
        if isinstance(url, str) and _hostname(url) == "shhaiid4u.net":
            try:
                from downloader.shhaiid4u_player_bridge import resolve

                candidates = await asyncio.to_thread(
                    resolve,
                    url,
                    validator=bot_module.validate_public_http_url,
                )
                if candidates:
                    max_size = (
                        bot_module.MAX_AUDIO_DOWNLOAD_BYTES
                        if is_audio
                        else bot_module.MAX_VIDEO_DOWNLOAD_BYTES
                    )
                    path, diagnostics = await _download_with_adapters(
                        candidates,
                        source_url=url,
                        temp_dir=temp_dir,
                        is_audio=is_audio,
                        max_size=max_size,
                        validator=bot_module.validate_public_http_url,
                    )
                    if path:
                        return path, "", "", {
                            "attempt_id": attempt_id,
                            "attempt_number": attempt_number,
                            "candidate_count": len(candidates),
                            "candidates": diagnostics.get("providers", []),
                            "provider_handoff": diagnostics,
                            "total_duration_ms": 0,
                        }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"⚠️ Shhaiid4u Provider Adapter: failed "
                    f"({type(exc).__name__})",
                    flush=True,
                )
        return await original(
            url,
            temp_dir,
            output_template,
            format_option,
            is_audio=is_audio,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
        )

    wrapped._shhaiid4u_provider_downloader = True
    bot_module.download_with_fallback = wrapped
    print(
        "🎯 Shhaiid4u Provider Adapter Architecture: ENABLED",
        flush=True,
    )
