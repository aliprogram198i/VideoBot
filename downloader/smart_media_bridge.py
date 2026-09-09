"""Async-safe bridge between the legacy media extractor and Smart Search."""

from __future__ import annotations

import asyncio
import inspect


def install(bot_module) -> None:
    """Wrap the legacy extractor and add conservative smart fallbacks."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(original) or getattr(original, "_smart_search_bridge", False):
        return

    resolver = __import__("downloader.smart_media_resolver", fromlist=["resolve"])
    browser_resolver = __import__(
        "downloader.browser_media_resolver",
        fromlist=["resolve"],
    )
    cobalt_resolver = __import__("downloader.cobalt_resolver", fromlist=["resolve"])

    async def wrapped(url, *args, **kwargs):
        # Preserve the existing production extractor as the first and safest
        # path. All new layers are strictly fallbacks.
        try:
            existing = original(url, *args, **kwargs)
            if inspect.isawaitable(existing):
                existing = await existing
        except Exception as exc:
            print(
                f"⚠️ Smart Search legacy extractor failed: {type(exc).__name__}",
                flush=True,
            )
            existing = []

        if existing:
            return existing

        # Static HTML/embed/yt-dlp resolver remains the first fallback because
        # it is cheaper and does not launch a browser.
        try:
            resolved = await asyncio.to_thread(
                resolver.resolve,
                url,
                validator=bot_module.validate_public_http_url,
                request_factory=bot_module.Request,
                open_function=bot_module.safe_urlopen,
                read_function=bot_module.read_limited,
            )
        except Exception as exc:
            print(
                f"⚠️ Smart Search Resolver failed: {type(exc).__name__}",
                flush=True,
            )
            resolved = []

        if resolved:
            print(
                f"🔎 Smart Search Resolver: resolved {len(resolved)} public media candidate(s)",
                flush=True,
            )
            return resolved

        # Browser fallback for JavaScript-driven public players.
        try:
            browser_resolved = await asyncio.to_thread(
                browser_resolver.resolve,
                url,
                validator=bot_module.validate_public_http_url,
            )
        except Exception as exc:
            print(
                f"⚠️ Browser Media Resolver failed: {type(exc).__name__}",
                flush=True,
            )
            browser_resolved = []

        if browser_resolved:
            print(
                f"🌐 Browser Media Resolver: resolved {len(browser_resolved)} public media candidate(s)",
                flush=True,
            )
            return browser_resolved

        # Final independent API fallback. It returns a normal public/tunnel
        # URL and hands that URL back to the existing downloader unchanged.
        try:
            cobalt_resolved = await asyncio.to_thread(cobalt_resolver.resolve, url)
        except Exception as exc:
            print(
                f"⚠️ Cobalt Resolver failed: {type(exc).__name__}",
                flush=True,
            )
            cobalt_resolved = []

        if cobalt_resolved:
            print(
                f"🧩 Cobalt Resolver: resolved {len(cobalt_resolved)} public media candidate(s)",
                flush=True,
            )
            return cobalt_resolved

        print("🔎 Smart Search: no public media candidate resolved", flush=True)
        return []

    wrapped._smart_search_bridge = True
    bot_module.extract_direct_media_urls = wrapped
    print("🔎 Smart Search async bridge: ENABLED", flush=True)
