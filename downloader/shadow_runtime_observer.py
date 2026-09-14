"""Fail-open runtime observer for resolver shadow decisions and staging evidence.

The observer never changes the live resolver order and never uses shadow output to
serve a request. Paired evidence is collected at a real download-request boundary
when available, after the user's download operation completes, so successful
primary paths can be included as well as fallback paths.
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
_PAIRED_PROBE_ORDER = _ELIGIBLE_ORDER


def _source_url(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    value = kwargs.get("url")
    if value is None and args:
        value = args[0]
    return value if isinstance(value, str) else None


def _request_context(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str | None, str]:
    """Extract a URL from the actual download call without assuming its signature."""
    source_url = None
    media_kind = "unknown"

    for key in ("url", "source_url", "video_url", "media_url"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            source_url = value
            break

    if source_url is None:
        for value in args:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                source_url = value
                break

    for key in ("media_kind", "media_type", "kind"):
        value = kwargs.get(key)
        if isinstance(value, str) and value in {"video", "audio", "hls", "dash", "progressive", "iframe", "unknown"}:
            media_kind = value
            break

    return source_url, media_kind


def _build_paired_probes(bot_module):
    """Build isolated probes using explicit resolver callables only."""
    try:
        legacy_resolver = getattr(bot_module, "_alibot_legacy_extractor_probe", None)
        resolver = __import__("downloader.smart_media_resolver", fromlist=["resolve"])
        browser_resolver = __import__("downloader.browser_media_resolver", fromlist=["resolve"])
        cobalt_resolver = __import__("downloader.cobalt_resolver", fromlist=["resolve"])
    except Exception:
        return {}

    probes = {}
    if callable(legacy_resolver):
        async def legacy_extractor(source_url: str):
            result = legacy_resolver(source_url)
            if inspect.isawaitable(result):
                result = await result
            return result
        probes["legacy_extractor"] = legacy_extractor

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

    probes.update({
        "smart_media": smart_media,
        "browser_media": browser_media,
        "cobalt": cobalt,
    })
    return probes


def install(bot_module) -> None:
    """Install extraction shadow observation and staging evidence collection."""
    original_extractor = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original_extractor) or getattr(original_extractor, "_shadow_runtime_observer", False):
        return

    evidence = ResolverEvidenceStore()
    decisions = ResolverShadowDecisionStore(evidence.db_path)

    def _schedule_paired_collection(url: str, platform: str, media_kind: str) -> None:
        """Schedule staging-only paired probes without adding request latency."""
        try:
            from .paired_evidence_collector import collect, enabled, sample_rate
            if not enabled():
                return
            probes = _build_paired_probes(bot_module)
            if len(probes) < 2:
                print("⚠️ Paired Evidence Collector: fewer than 2 resolver probes available.", flush=True)
                return
            print(
                f"🧪 Paired Evidence Collector: scheduling real request "
                f"platform={platform} media_kind={media_kind} rate={sample_rate():.3f}",
                flush=True,
            )
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

            def _report_collection(completed):
                if completed.cancelled():
                    print("⚠️ Paired Evidence Collector: task cancelled.", flush=True)
                    return
                try:
                    print(f"🧪 Paired Evidence Collector: task finished stored={completed.result()}", flush=True)
                except Exception as exc:
                    print(f"⚠️ Paired Evidence Collector: task failed {type(exc).__name__}.", flush=True)

            task.add_done_callback(_report_collection)
        except Exception as exc:
            print(f"⚠️ Paired Evidence Collector: scheduling skipped {type(exc).__name__}.", flush=True)

    def _observe(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """Persist bounded shadow data at the extraction boundary; never schedule probes here."""
        try:
            url = _source_url(args, kwargs)
            if not url:
                return
            from .smart_media_bridge import _resolver_context
            platform, media_kind = _resolver_context(url, "unknown")
            validations = validate_resolver_set(evidence, _ELIGIBLE_ORDER, platform=platform, media_kind=media_kind)
            decision = evaluate_shadow(_ELIGIBLE_ORDER, validations, platform=platform, media_kind=media_kind)
            decisions.record(decision)
        except Exception:
            return

    async def observed(*args, **kwargs):
        try:
            result = original_extractor(*args, **kwargs)
        except Exception:
            _observe(args, kwargs)
            raise
        if inspect.isawaitable(result):
            try:
                result = await result
            finally:
                _observe(args, kwargs)
        else:
            _observe(args, kwargs)
        return result

    observed._shadow_runtime_observer = True
    bot_module.extract_direct_media_urls = observed
    print("🧪 Resolver Shadow Runtime Observer: ENABLED (fail-open, no resolver changes)", flush=True)

    # The actual bot download path uses download_with_fallback(), not
    # download_media(). The previous hook therefore never attached to the real
    # request boundary, which explains why successful real traffic did not create
    # new paired-evidence scheduling events. Keep compatibility with a future
    # download_media() API, but prefer the concrete function that exists today.
    download_hook_name = None
    original_download = getattr(bot_module, "download_with_fallback", None)
    if callable(original_download):
        download_hook_name = "download_with_fallback"
    else:
        original_download = getattr(bot_module, "download_media", None)
        if callable(original_download):
            download_hook_name = "download_media"

    if callable(original_download) and not getattr(original_download, "_paired_evidence_request_observer", False):
        async def observed_download(*args, **kwargs):
            try:
                result = original_download(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result
            finally:
                try:
                    source_url, media_kind = _request_context(args, kwargs)
                    if source_url:
                        from .smart_media_bridge import _resolver_context
                        platform, _ = _resolver_context(source_url, media_kind)
                        _schedule_paired_collection(source_url, platform, media_kind)
                    else:
                        print("⚠️ Paired Evidence Collector: download request had no URL argument.", flush=True)
                except Exception as exc:
                    print(f"⚠️ Paired Evidence Collector: request observation skipped {type(exc).__name__}.", flush=True)

        observed_download._paired_evidence_request_observer = True
        setattr(bot_module, download_hook_name, observed_download)
        print(
            f"🧪 Paired Evidence Collector: observing {download_hook_name} "
            "(post-request, fail-open)",
            flush=True,
        )
    else:
        print("⚠️ Paired Evidence Collector: no concrete download boundary found during install.", flush=True)


__all__ = ["install"]
