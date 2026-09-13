"""Runtime-neutral observer for the dormant resolver shadow policy.

This layer observes the established extraction call after the existing resolver
bridge has run. It never changes resolver order, invokes an extra resolver, or
creates a network request. Statistical validation is computed only from paired
evidence already persisted by the evidence layer.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from .resolver_evidence import ResolverEvidenceStore
from .resolver_shadow_decision import ResolverShadowDecisionStore, evaluate_shadow
from .resolver_statistical_validation import validate_resolver_set

_ELIGIBLE_ORDER = ("legacy_extractor", "smart_media", "browser_media", "cobalt")


def _source_url(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    value = kwargs.get("url")
    if value is None and args:
        value = args[0]
    return value if isinstance(value, str) else None


def install(bot_module) -> None:
    """Install one fail-open observation wrapper around the existing bridge."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_shadow_runtime_observer", False):
        return

    evidence = ResolverEvidenceStore()
    decisions = ResolverShadowDecisionStore(evidence.db_path)

    async def observed(*args, **kwargs):
        started = time.monotonic()
        try:
            result = original(*args, **kwargs)
        except Exception:
            _observe(args, kwargs, started)
            raise
        if inspect.isawaitable(result):
            try:
                result = await result
            finally:
                _observe(args, kwargs, started)
        else:
            _observe(args, kwargs, started)
        return result

    def _observe(args: tuple[Any, ...], kwargs: dict[str, Any], started: float) -> None:
        """Persist only a bounded hypothetical decision; all failures are ignored."""
        try:
            url = _source_url(args, kwargs)
            if not url:
                return
            from .smart_media_bridge import _resolver_context

            platform, media_kind = _resolver_context(url, "unknown")
            validations = validate_resolver_set(
                evidence,
                _ELIGIBLE_ORDER,
                platform=platform,
                media_kind=media_kind,
            )
            decision = evaluate_shadow(
                _ELIGIBLE_ORDER,
                validations,
                platform=platform,
                media_kind=media_kind,
            )
            decisions.record(decision)
        except Exception:
            return

    observed._shadow_runtime_observer = True
    bot_module.extract_direct_media_urls = observed
    print("🧪 Resolver Shadow Runtime Observer: ENABLED (fail-open, no resolver changes)", flush=True)


__all__ = ["install"]
