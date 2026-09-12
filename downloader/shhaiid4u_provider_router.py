"""Canonical provider router for public Shhaiid4u candidates.

Shhaiid4u is treated as a source page, not as a media provider. This layer
identifies the actual public provider by hostname and hands only owned
providers to the existing isolated provider downloader.

The router is deliberately narrow: it does not alter generic platforms,
YouTube, Yoinku, Cobalt, the database, or Telegram delivery.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    hosts: tuple[str, ...]
    enabled: bool = True


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("megaup", ("megaup.net",)),
    ProviderSpec("streamtape", ("streamtape.com",)),
)


def hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().rstrip(".")
    except Exception:
        return ""


def _host_matches(host: str, owned: tuple[str, ...]) -> bool:
    return any(host == item or host.endswith("." + item) for item in owned)


def identify_provider(url: str) -> ProviderSpec | None:
    host = hostname(url)
    if not host:
        return None
    for provider in PROVIDERS:
        if provider.enabled and _host_matches(host, provider.hosts):
            return provider
    return None


def route_candidates(candidates: list[str]) -> dict[str, list[str]]:
    """Group supported candidates by provider and deduplicate exact URLs."""
    routed: dict[str, list[str]] = {}
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or candidate in seen:
            continue
        provider = identify_provider(candidate)
        if provider is None:
            continue
        seen.add(candidate)
        routed.setdefault(provider.provider_id, []).append(candidate)
    return routed


def owned_candidates(candidates: list[str]) -> list[str]:
    """Return supported candidates in deterministic provider order."""
    routed = route_candidates(candidates)
    result: list[str] = []
    for provider in PROVIDERS:
        result.extend(routed.get(provider.provider_id, []))
    return result


async def _resolve_and_download(*, url: str, temp_dir: str, is_audio: bool, max_size: int, validator):
    from downloader.shhaiid4u_player_bridge import resolve
    from downloader.shhaiid4u_provider_downloader import download_candidates

    print("🎯 Shhaiid4u Provider Router: resolving provider candidates", flush=True)
    discovered = await asyncio.to_thread(resolve, url, validator=validator)
    ordered = owned_candidates(discovered)
    print(
        f"🎯 Shhaiid4u Provider Router: discovered={len(discovered)} owned={len(ordered)}",
        flush=True,
    )
    if not ordered:
        return None, {
            "status": "skipped",
            "reason": "no_supported_provider",
            "discovered_count": len(discovered),
            "candidate_count": 0,
        }

    path, diagnostics = await download_candidates(
        ordered[:6],
        source_url=url,
        temp_dir=temp_dir,
        is_audio=is_audio,
        max_size=max_size,
    )
    diagnostics = dict(diagnostics or {})
    diagnostics["router"] = {
        "providers": sorted({identify_provider(item).provider_id for item in ordered if identify_provider(item)}),
        "candidate_count": min(len(ordered), 6),
        "discovered_count": len(discovered),
    }
    return path, diagnostics


def install(bot_module) -> None:
    """Install one canonical Shhaiid4u boundary around download_with_fallback."""
    original = getattr(bot_module, "download_with_fallback", None)
    if not callable(original) or getattr(original, "_shhaiid4u_provider_router", False):
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
        if isinstance(url, str) and hostname(url) == "shhaiid4u.net":
            try:
                path, diagnostics = await _resolve_and_download(
                    url=url,
                    temp_dir=temp_dir,
                    is_audio=is_audio,
                    max_size=(
                        bot_module.MAX_AUDIO_DOWNLOAD_BYTES
                        if is_audio
                        else bot_module.MAX_VIDEO_DOWNLOAD_BYTES
                    ),
                    validator=bot_module.validate_public_http_url,
                )
                if path:
                    print("✅ Shhaiid4u Provider Router: verified provider file", flush=True)
                    return path, "", "", {
                        "attempt_id": attempt_id,
                        "attempt_number": attempt_number,
                        "candidate_count": diagnostics.get("router", {}).get("candidate_count", 0),
                        "candidates": diagnostics.get("providers", []),
                        "provider_handoff": diagnostics,
                        "total_duration_ms": 0,
                    }
                print("⚠️ Shhaiid4u Provider Router: no verified provider file; preserving generic fallback", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ Shhaiid4u Provider Router: failed ({type(exc).__name__}); preserving generic fallback", flush=True)

        result = original(url, temp_dir, output_template, format_option, is_audio, attempt_id, attempt_number)
        if inspect.isawaitable(result):
            result = await result
        return result

    wrapped._shhaiid4u_provider_router = True
    bot_module.download_with_fallback = wrapped
    print("🎯 Shhaiid4u Provider Router: ENABLED", flush=True)
