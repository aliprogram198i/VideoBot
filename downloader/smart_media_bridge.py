"""Async-safe bridge between the legacy media extractor and Smart Search."""

from __future__ import annotations

import asyncio
import inspect


def install(bot_module) -> None:
    """Compose the legacy extractor, Smart Search, browser, and Cobalt fallbacks.

    The bridge is installed after the complete bot module is imported, so it
    can safely wrap both the legacy direct-media extractor and the existing
    Smart Extraction entrypoint without modifying the production download or
    Telegram upload code paths.
    """
    original = getattr(bot_module, "extract_direct_media_urls", None)
    original_smart = getattr(bot_module, "download_with_smart_extraction", None)
    original_fallback = getattr(bot_module, "download_with_fallback", None)

    if not callable(original) or getattr(original, "_smart_search_bridge", False):
        return

    resolver = __import__("downloader.smart_media_resolver", fromlist=["resolve"])
    browser_resolver = __import__(
        "downloader.browser_media_resolver",
        fromlist=["resolve"],
    )
    cobalt_resolver = __import__("downloader.cobalt_resolver", fromlist=["resolve"])

    async def wrapped(url, *args, **kwargs):
        print("🔎 Smart Media Bridge: entered", flush=True)

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
            print(
                f"🔎 Smart Media Bridge: legacy returned {len(existing)} candidate(s)",
                flush=True,
            )
            return existing

        # Static HTML/embed/yt-dlp resolver remains the first fallback because
        # it is cheaper and does not launch a browser.
        try:
            print("🔎 Smart Media Bridge: static resolver starting", flush=True)
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

        # Browser fallback for JavaScript-driven public players and explicit
        # public download/server chains. This is intentionally unconditional
        # after the static resolver so an extractor exception cannot suppress
        # the browser layer.
        try:
            print("🌐 Smart Media Bridge: browser resolver starting", flush=True)
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
            print("🧩 Smart Media Bridge: Cobalt resolver starting", flush=True)
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

    # Critical handoff: the existing Smart Extraction function is a separate
    # path and can finish with no candidate before download_with_fallback() is
    # ever reached. Wrap it so a failed static/yt-dlp smart pass immediately
    # hands control to the same direct-media/browser chain instead of waiting
    # behind the Yoinku stage.
    if callable(original_smart) and callable(original_fallback):
        async def wrapped_smart(*args, **kwargs):
            print("🔎 Smart Media Bridge: smart-extraction handoff active", flush=True)

            try:
                smart_result = original_smart(*args, **kwargs)
            except Exception as exc:
                print(
                    f"⚠️ Smart Extraction handoff exception: {type(exc).__name__}",
                    flush=True,
                )
                smart_result = None

            if inspect.isawaitable(smart_result):
                try:
                    smart_result = await smart_result
                except Exception as exc:
                    print(
                        f"⚠️ Smart Extraction handoff await failed: {type(exc).__name__}",
                        flush=True,
                    )
                    smart_result = None

            if isinstance(smart_result, tuple) and smart_result and smart_result[0]:
                return smart_result

            url = kwargs.get("url")
            if url is None and args:
                url = args[0]

            if not url:
                print("⚠️ Smart Media Bridge: no URL available for handoff", flush=True)
                return smart_result

            print(
                "🌐 Smart Media Bridge: handing failed Smart Extraction to direct-media chain",
                flush=True,
            )

            fallback_kwargs = {
                "url": url,
                "temp_dir": kwargs.get("temp_dir"),
                "output_template": kwargs.get("output_template"),
                "format_option": kwargs.get("format_option"),
                "is_audio": kwargs.get("is_audio", False),
                "attempt_id": kwargs.get("attempt_id"),
                "attempt_number": kwargs.get("attempt_number"),
            }

            try:
                return_value = original_fallback(**fallback_kwargs)
                if inspect.isawaitable(return_value):
                    return_value = await return_value
                if isinstance(return_value, tuple) and return_value and return_value[0]:
                    print(
                        "🌐 Smart Media Bridge: direct-media handoff succeeded",
                        flush=True,
                    )
                    return return_value[0], {
                        "handoff": "direct_media_chain",
                        "fallback_diagnostics": return_value[3] if len(return_value) > 3 else {},
                    }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"⚠️ Smart Media Bridge: direct-media handoff failed: {type(exc).__name__}",
                    flush=True,
                )

            return smart_result

        wrapped_smart._smart_search_bridge = True
        bot_module.download_with_smart_extraction = wrapped_smart

    print("🔎 Smart Search async bridge: ENABLED", flush=True)
    if callable(original_smart) and callable(original_fallback):
        print("🌐 Smart Media Bridge: Smart Extraction handoff ENABLED", flush=True)
