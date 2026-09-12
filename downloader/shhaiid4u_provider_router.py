"""Provider router for public Shhaiid4u candidates.

Shhaiid4u is treated as a source page, not as a media provider. The router
identifies the actual provider by canonical hostname and hands only owned,
public provider URLs to the existing provider-specific downloader logic.

No authentication, CAPTCHA solving, DRM bypass, cookie injection, or access
control circumvention is performed here.
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


# These hosts deliberately mirror the providers that have a verified,
# public downloader implementation in shhaiid4u_provider_downloader.py.
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
    """Group owned candidates by provider without changing candidate URLs."""
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
    """Return owned candidates in deterministic provider order."""
    routed = route_candidates(candidates)
    result: list[str] = []
    for provider in PROVIDERS:
        result.extend(routed.get(provider.provider_id, []))
    return result


async def _resolve_and_download(
    *,
    url: str,
    temp_dir: str,
    is_audio: bool,
    max_size: int,
    validator,
):
    from downloader.shhaiid4u_player_bridge import resolve
    from downloader.shhaiid4u_provider_downloader import download_candidates

    discovered = await asyncio.to_thread(resolve, url, validator=validator)
    routed = route_candidates(discovered)
    if not routed:
        return None, {
            "status": "skipped",
            "reason": "no_supported_provider",
            "discovered": len(discovered),
        }

    ordered = [candidate for candidate in discovered if identify_provider(candidate) is not None]
    path, diagnostics = await download_candidates(
        ordered,
        source_url=url,
        temp_dir=temp_dir,
        is_audio=is_audio,
        max_size=max_size,
    )
    diagnostics = dict(diagnostics or {})
    diagnostics["router"] = {
        "providers": sorted(routed),
        "candidate_count": len(ordered),
        "discovered_count": len(discovered),
    }
    return path, diagnostics


def install(bot_module) -> None:
    """Install one outer Shhaiid4u provider boundary; other platforms are untouched."""
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
                    return path, "", "", {
                        "attempt_id": attempt_id,
                        "attempt_number": attempt_number,
                        "candidate_count": diagnostics.get("router", {}).get("candidate_count", 0),
                        "candidates": diagnostics.get("providers", []),
                        "provider_handoff": diagnostics,
                        "total_duration_ms": 0,
                    }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"⚠️ Shhaiid4u Provider Router: failed ({type(exc).__name__})",
                    flush=True,
                )
        result = original(url, temp_dir, output_template, format_option, is_audio, attempt_id, attempt_number)
        if inspect.isawaitable(result):
            result = await result
        return result

    wrapped._shhaiid4u_provider_router = True
    bot_module.download_with_fallback = wrapped
    print("🎯 Shhaiid4u Provider Router: ENABLED", flush=True)
