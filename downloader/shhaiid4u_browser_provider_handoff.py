"""Bounded browser handoff for Shhaiid4u's public provider URLs.

This layer is deliberately narrow: it owns only Shhaiid4u source pages and
only provider hosts already discovered by the Shhaiid4u player bridge. It
reuses the existing Browser Download Handoff so provider pages can preserve
referrer/browser context instead of forcing every provider through yt-dlp.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse


SUPPORTED_HOSTS = {"megaup.net", "streamtape.com"}
MAX_CANDIDATES = 6
TIMEOUT_MS = 45_000
SETTLE_MS = 2_000


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().rstrip(".")
    except Exception:
        return ""


def is_supported_provider(url: str) -> bool:
    host = _hostname(url)
    return host in SUPPORTED_HOSTS or any(host.endswith("." + item) for item in SUPPORTED_HOSTS)


def _owned_candidates(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        candidate = value.get("url") if isinstance(value, dict) else value
        if not isinstance(candidate, str):
            continue
        if not candidate.startswith(("http://", "https://")):
            continue
        if not is_supported_provider(candidate) or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
        if len(result) >= MAX_CANDIDATES:
            break
    return result


async def _try_candidates(
    candidates,
    *,
    source_url: str,
    temp_dir: str,
    validator,
    is_audio: bool,
    max_file_bytes: int,
):
    owned = _owned_candidates(candidates)
    if not owned or not isinstance(source_url, str) or not temp_dir:
        return None, {"status": "skipped", "reason": "no_owned_candidates"}

    try:
        from downloader import browser_download_handoff
    except Exception:
        return None, {"status": "failed", "reason": "browser_handoff_unavailable"}

    diagnostics = {
        "status": "failed",
        "candidate_count": len(owned),
        "providers": [],
    }

    for candidate in owned:
        provider = _hostname(candidate)
        entry = {"provider": provider, "candidate": candidate, "status": "failed"}
        diagnostics["providers"].append(entry)
        try:
            path = await browser_download_handoff.resolve_to_file(
                candidate,
                temp_dir,
                validator=validator,
                is_audio=is_audio,
                max_file_bytes=max_file_bytes,
                timeout_ms=TIMEOUT_MS,
                settle_ms=SETTLE_MS,
                referer_url=source_url,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            entry["error"] = type(exc).__name__
            continue
        if path:
            entry["status"] = "success"
            diagnostics["status"] = "success"
            return path, diagnostics

    return None, diagnostics


def install(bot_module) -> None:
    """Install an outer, Shhaiid4u-only browser provider fallback."""
    original = getattr(bot_module, "download_with_fallback", None)
    if not callable(original) or getattr(original, "_shhaiid4u_browser_provider_handoff", False):
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
                    print(
                        f"🎯 Shhaiid4u Browser Provider Handoff: trying {len(candidates)} discovered candidate(s)",
                        flush=True,
                    )
                    path, diagnostics = await _try_candidates(
                        candidates,
                        source_url=url,
                        temp_dir=temp_dir,
                        validator=bot_module.validate_public_http_url,
                        is_audio=is_audio,
                        max_file_bytes=(
                            bot_module.MAX_AUDIO_DOWNLOAD_BYTES
                            if is_audio
                            else bot_module.MAX_VIDEO_DOWNLOAD_BYTES
                        ),
                    )
                    if path:
                        print(
                            f"✅ Shhaiid4u Browser Provider Handoff: success via {diagnostics['providers'][-1]['provider']}",
                            flush=True,
                        )
                        return path, "", "", {
                            "attempt_id": attempt_id,
                            "attempt_number": attempt_number,
                            "candidate_count": len(candidates),
                            "candidates": diagnostics["providers"],
                            "provider_handoff": diagnostics,
                            "total_duration_ms": 0,
                        }
                    print(
                        "⚠️ Shhaiid4u Browser Provider Handoff: no verified provider file",
                        flush=True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"⚠️ Shhaiid4u Browser Provider Handoff: failed ({type(exc).__name__})",
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

    wrapped._shhaiid4u_browser_provider_handoff = True
    bot_module.download_with_fallback = wrapped
    print("🎯 Shhaiid4u Browser Provider Handoff: ENABLED", flush=True)
