"""Fail-open runtime observer for resolver shadow decisions and staging evidence.

The observer never changes the live resolver order and never uses shadow output to
serve a request. Paired evidence collection is separately gated to staging and
runs only as a background task after the established extraction call completes.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from .resolver_evidence import ResolverEvidenceStore
from .resolver_shadow_decision import ResolverShadowDecisionStore, evaluate_shadow
from .resolver_statistical_validation import validate_resolver_set

_ELIGIBLE_ORDER = ("legacy_extractor", "smart_media", "browser_media", "cobalt")
_PAIRED_PROBE_ORDER = ("smart_media", "browser_media", "cobalt")


def _source_url(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    value = kwargs.get("url")
    if value is None and args:
        value = args[0]
    return value if isinstance(value, str) else None


def _build_paired_probes(bot_module):
    """Build isolated probes for resolvers whose call signatures are stable.

    The legacy extractor is intentionally not guessed through closure inspection.
    It will be added only when the bridge exposes an explicit probe hook. This
    keeps evidence collection correct rather than pretending four-way evidence is
    available when it is not.
    """
    try:
        resolver = __import__("downloader.smart_media_resolver", fromlist=["resolve"])
        browser_resolver = __import__("downloader.browser_media_resolver", fromlist=["resolve"])
        cobalt_resolver = __import__("downloader.cobalt_resolver", fromlist=["resolve"])
    except Exception:
        return {}

    async def smart_media(source_url: str):
        result = resolver.resolve(
            source_url,
            validator=bot_module.validate_public_http_url,
            request_factory=bot_module.Request,
            open_function=bot_module.safe_urlopen,
            read_function=bot_module.read_limited,
            source_url=source_url,
            media_kind="unknown",
        )
        if inspect.isawaitable(result):
            result = await result
        return result

    async def browser_media(source_url: str):
        result = browser_resolver.resolve(
            source_url,
            validator=bot_module.validate_public_http_url,
            source_url=source_url,
            media_kind="iframe",
        )
        if inspect.isawaitable(result):
            result = await result
        return result

    async def cobalt(source_url: str):
        result = cobalt_resolver.resolve(
            source_url,
            source_url=source_url,
            media_kind="unknown",
        )
        if inspect.isawaitable(result):
            result = await result
        return result

    return {
        "smart_media": smart_media,
        "browser_media": browser_media,
        "cobalt": cobalt,
    }


def install(bot_module) -> None:
    """Install one fail-open observer around the established extraction bridge."""
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

    def _schedule_paired_collection(url: str, platform: str, media_kind: str) -> None:
        """Schedule staging-only paired probes without adding request latency."""
        try:
            from .paired_evidence_collector import collect, enabled

            if not enabled():
                return
            probes = _build_paired_probes(bot_module)
            if len(probes) < 2:
                return
            task = asyncio.create_task(
                collect(
                    evidence,
                    sample_id=None,
                    platform=platform,
                    media_kind=media_kind,
                    source_url=url,
                    resolvers={name: probes[name] for name in _PAIRED_PROBE_ORDER if name in probes},
                )
            )
            task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
        except Exception:
            return

    def _observe(args: tuple[Any, ...], kwargs: dict[str, Any], started: float) -> None:
        """Persist only bounded shadow data; all observer failures are ignored."""
        del started
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
            _schedule_paired_collection(url, platform, media_kind)
        except Exception:
            return

    observed._shadow_runtime_observer = True
    bot_module.extract_direct_media_urls = observed
    print("🧪 Resolver Shadow Runtime Observer: ENABLED (fail-open, no resolver changes)", flush=True)


__all__ = ["install"]
