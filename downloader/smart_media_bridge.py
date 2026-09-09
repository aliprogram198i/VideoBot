"""Async-safe bridge between the legacy media extractor and Smart Search."""

from __future__ import annotations

import asyncio
import inspect
import os


def install(bot_module) -> None:
    """Compose the legacy extractor, Smart Search, browser, and Cobalt fallbacks."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    original_smart = getattr(bot_module, "download_with_smart_extraction", None)
    original_fallback = getattr(bot_module, "download_with_fallback", None)

    if not callable(original) or getattr(original, "_smart_search_bridge", False):
        return

    resolver = __import__("downloader.smart_media_resolver", fromlist=["resolve"])
    browser_resolver = __import__("downloader.browser_media_resolver", fromlist=["resolve"])
    browser_handoff = __import__("downloader.browser_download_handoff", fromlist=["resolve_to_file"])
    cobalt_resolver = __import__("downloader.cobalt_resolver", fromlist=["resolve"])

    async def wrapped(url, *args, **kwargs):
        print("🔎 Smart Media Bridge: entered", flush=True)
        try:
            existing = original(url, *args, **kwargs)
            if inspect.isawaitable(existing):
                existing = await existing
        except Exception as exc:
            print(f"⚠️ Smart Search legacy extractor failed: {type(exc).__name__}", flush=True)
            existing = []
        if existing:
            print(f"🔎 Smart Media Bridge: legacy returned {len(existing)} candidate(s)", flush=True)
            return existing
        try:
            print("🔎 Smart Media Bridge: static resolver starting", flush=True)
            resolved = await asyncio.to_thread(resolver.resolve, url, validator=bot_module.validate_public_http_url, request_factory=bot_module.Request, open_function=bot_module.safe_urlopen, read_function=bot_module.read_limited)
        except Exception as exc:
            print(f"⚠️ Smart Search Resolver failed: {type(exc).__name__}", flush=True)
            resolved = []
        if resolved:
            print(f"🔎 Smart Search Resolver: resolved {len(resolved)} public media candidate(s)", flush=True)
            return resolved
        try:
            print("🌐 Smart Media Bridge: browser resolver starting", flush=True)
            browser_resolved = await asyncio.to_thread(browser_resolver.resolve, url, validator=bot_module.validate_public_http_url)
        except Exception as exc:
            print(f"⚠️ Browser Media Resolver failed: {type(exc).__name__}", flush=True)
            browser_resolved = []
        if browser_resolved:
            print(f"🌐 Browser Media Resolver: resolved {len(browser_resolved)} public media candidate(s)", flush=True)
            return browser_resolved
        try:
            print("🧩 Smart Media Bridge: Cobalt resolver starting", flush=True)
            cobalt_resolved = await asyncio.to_thread(cobalt_resolver.resolve, url)
        except Exception as exc:
            print(f"⚠️ Cobalt Resolver failed: {type(exc).__name__}", flush=True)
            cobalt_resolved = []
        if cobalt_resolved:
            print(f"🧩 Cobalt Resolver: resolved {len(cobalt_resolved)} public media candidate(s)", flush=True)
            return cobalt_resolved
        print("🔎 Smart Media Search: no public media candidate resolved", flush=True)
        return []

    wrapped._smart_search_bridge = True
    bot_module.extract_direct_media_urls = wrapped

    if callable(original_fallback):
        async def wrapped_fallback(*args, **kwargs):
            result = original_fallback(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, tuple) and result and result[0]:
                return result

            temp_dir = kwargs.get("temp_dir")
            is_audio = bool(kwargs.get("is_audio", False))
            diagnostics = result[3] if isinstance(result, tuple) and len(result) > 3 else {}
            candidates = diagnostics.get("candidates", []) if isinstance(diagnostics, dict) else []
            if not temp_dir or not candidates:
                return result

            print("🌐 Browser Download Handoff: normal direct download produced no file", flush=True)
            max_bytes = getattr(bot_module, "MAX_AUDIO_DOWNLOAD_BYTES" if is_audio else "MAX_VIDEO_DOWNLOAD_BYTES", 500 * 1024 * 1024)
            source_url = kwargs.get("url")
            if source_url is None and args:
                source_url = args[0]
            candidate_urls = []
            for item in candidates:
                candidate = item.get("url") if isinstance(item, dict) else item
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://")) and candidate not in candidate_urls:
                    candidate_urls.append(candidate)

            for candidate in candidate_urls[:8]:
                try:
                    local_path = await asyncio.to_thread(
                        browser_handoff.resolve_to_file,
                        candidate,
                        temp_dir,
                        validator=bot_module.validate_public_http_url,
                        is_audio=is_audio,
                        max_file_bytes=max_bytes,
                        referer_url=source_url if isinstance(source_url, str) else None,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"⚠️ Browser Download Handoff failed: {type(exc).__name__}", flush=True)
                    local_path = None
                if local_path and isinstance(local_path, str):
                    try:
                        size = os.path.getsize(local_path)
                    except OSError:
                        size = 0
                    if size > 0:
                        print(f"🌐 Browser Download Handoff: succeeded ({size} bytes)", flush=True)
                        handoff_diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
                        handoff_diagnostics.update({"status": "browser_download_handoff_success", "handoff_candidate": candidate, "bytes_downloaded": size})
                        return local_path, "Browser Download Handoff: saved local file", "", handoff_diagnostics

            print("🌐 Browser Download Handoff: no usable browser file", flush=True)
            return result

        wrapped_fallback._smart_search_bridge = True
        bot_module.download_with_fallback = wrapped_fallback
    else:
        wrapped_fallback = None

    if callable(original_smart) and callable(original_fallback):
        async def wrapped_smart(*args, **kwargs):
            print("🔎 Smart Media Bridge: smart-extraction handoff active", flush=True)
            try:
                smart_result = original_smart(*args, **kwargs)
                if inspect.isawaitable(smart_result):
                    smart_result = await smart_result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ Smart Extraction handoff failed: {type(exc).__name__}", flush=True)
                smart_result = None

            if isinstance(smart_result, tuple) and smart_result and smart_result[0]:
                return smart_result

            url = kwargs.get("url")
            if url is None and args:
                url = args[0]
            if not url:
                return smart_result

            print("🌐 Smart Media Bridge: handing failed Smart Extraction to direct-media chain", flush=True)
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
                if callable(wrapped_fallback):
                    return_value = wrapped_fallback(**fallback_kwargs)
                else:
                    return_value = original_fallback(**fallback_kwargs)
                if inspect.isawaitable(return_value):
                    return_value = await return_value
                if isinstance(return_value, tuple) and return_value and return_value[0]:
                    print("🌐 Smart Media Bridge: direct-media handoff succeeded", flush=True)
                    return return_value[0], {"handoff": "direct_media_chain", "fallback_diagnostics": return_value[3] if len(return_value) > 3 else {}}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ Smart Media Bridge: direct-media handoff failed: {type(exc).__name__}", flush=True)
            return smart_result

        wrapped_smart._smart_search_bridge = True
        bot_module.download_with_smart_extraction = wrapped_smart

    print("🔎 Smart Search async bridge: ENABLED", flush=True)
    if callable(original_smart) and callable(original_fallback):
        print("🌐 Smart Media Bridge: Smart Extraction handoff ENABLED", flush=True)
    if callable(original_fallback):
        print("🌐 Smart Media Bridge: Browser Download Handoff ENABLED", flush=True)
