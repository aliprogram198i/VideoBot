"""Async-safe bridge between the legacy media extractor and Smart Search."""

from __future__ import annotations

import inspect


def install(bot_module) -> None:
    """Wrap the legacy async extractor without changing its contract."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_smart_search_bridge", False):
        return

    resolver = __import__("downloader.smart_media_resolver", fromlist=["resolve"])

    async def wrapped(url, *args, **kwargs):
        try:
            existing = original(url, *args, **kwargs)
            if inspect.isawaitable(existing):
                existing = await existing
        except Exception as exc:
            print(f"⚠️ Smart Search legacy extractor failed: {type(exc).__name__}", flush=True)
            existing = []

        if existing:
            return existing

        try:
            resolved = await __import__("asyncio").to_thread(
                resolver.resolve,
                url,
                validator=bot_module.validate_public_http_url,
                request_factory=bot_module.Request,
                open_function=bot_module.safe_urlopen,
                read_function=bot_module.read_limited,
            )
        except Exception as exc:
            print(f"⚠️ Smart Search Resolver failed: {type(exc).__name__}", flush=True)
            return []

        if resolved:
            print(
                f"🔎 Smart Search Resolver: resolved {len(resolved)} public media candidate(s)",
                flush=True,
            )
        else:
            print("🔎 Smart Search Resolver: no public media candidate resolved", flush=True)
        return resolved

    wrapped._smart_search_bridge = True
    bot_module.extract_direct_media_urls = wrapped
    print("🔎 Smart Search async bridge: ENABLED", flush=True)
