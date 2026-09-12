"""Bounded adaptive ordering for already-discovered public media candidates.

This layer does not discover, download, or bypass anything. It only re-orders
candidates returned by the existing extraction chain using the immutable Smart
production policy. If the policy or candidate shape is unavailable, the original
ordering is preserved exactly.
"""

from __future__ import annotations

import inspect
from urllib.parse import urlparse

from .smart_learning import DEFAULT_POLICY_VERSION, SmartTelemetryStore

_MAX_CANDIDATES = 16


def _candidate_url(item):
    if isinstance(item, str) and item.startswith(("http://", "https://")):
        return item
    if isinstance(item, dict):
        value = item.get("url")
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _kind(url: str) -> str:
    path = (urlparse(url).path or "").lower()
    if ".m3u8" in path:
        return "hls"
    if ".mpd" in path:
        return "dash"
    if any(ext in path for ext in (".mp4", ".webm", ".mov", ".m4v")):
        return "progressive"
    return "iframe"


def _discovered_by(url: str) -> str:
    path = (urlparse(url).path or "").lower()
    if ".m3u8" in path or ".mpd" in path or any(
        ext in path for ext in (".mp4", ".webm", ".mov", ".m4v")
    ):
        return "video"
    return "iframe"


def _score(url: str, weights: dict[str, float]) -> float:
    kind = _kind(url)
    discovered = _discovered_by(url)
    score = weights.get(f"kind:{kind}", 0.0)
    score += weights.get(f"discovered:{discovered}", 0.0)
    return float(score)


def order_candidates(candidates, weights: dict[str, float] | None = None):
    """Return candidates in adaptive order while preserving stable ties.

    Non-URL values are never moved ahead of valid URLs. The function is bounded
    and deterministic, and returns the original object values unchanged.
    """
    if not isinstance(candidates, (list, tuple)) or len(candidates) < 2:
        return candidates
    original = list(candidates)
    if weights is None:
        weights = {}
    indexed = list(enumerate(original))
    scored = []
    for index, item in indexed:
        url = _candidate_url(item)
        score = _score(url, weights) if url else float("-inf")
        scored.append((score, -index, item))
    scored.sort(reverse=True)
    ordered = [item for _, _, item in scored]
    if len(ordered) > _MAX_CANDIDATES:
        # Bound the adaptive work without dropping or duplicating candidates.
        # The remainder stays in the same adaptive order rather than reverting
        # to the original tail, which could duplicate an item from the top-N set.
        return ordered[:_MAX_CANDIDATES] + ordered[_MAX_CANDIDATES:]
    return ordered


def install(bot_module, *, store_factory=None) -> None:
    """Install the ordering wrapper once around the existing extractor."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_adaptive_orchestrator", False):
        return
    factory = store_factory or SmartTelemetryStore

    async def wrapped(url, *args, **kwargs):
        result = original(url, *args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, (list, tuple)) or len(result) < 2:
            return result
        try:
            store = factory()
            version, weights = store.production_policy()
            ordered = order_candidates(result, weights)
            if list(result) != list(ordered):
                print(
                    f"🧠 Adaptive Download Orchestrator: reordered candidates ({version or DEFAULT_POLICY_VERSION})",
                    flush=True,
                )
            return type(result)(ordered) if isinstance(result, tuple) else ordered
        except Exception as exc:
            print(f"⚠️ Adaptive Download Orchestrator: advisory fallback ({type(exc).__name__})", flush=True)
            return result

    wrapped._adaptive_orchestrator = True
    bot_module.extract_direct_media_urls = wrapped
    print("🧠 Adaptive Download Orchestrator: ENABLED (bounded candidate ordering)", flush=True)
