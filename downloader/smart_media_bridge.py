"""Async-safe bridge between the legacy media extractor and Smart Search."""

from __future__ import annotations

import asyncio
import inspect
import os


def install(bot_module) -> None:
    """Compose legacy, provider-specific, static, browser, and Cobalt fallbacks."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    original_smart = getattr(bot_module, "download_with_smart_extraction", None)
    original_fallback = getattr(bot_module, "download_with_fallback", None)

    if not callable(original) or getattr(original, "_smart_search_bridge", False):
        return

    resolver = __import__("downloader.smart_media_resolver", fromlist=["resolve"])
    shahid4u_resolver = __import__("downloader.shahid4u_resolver", fromlist=["resolve"])
    browser_resolver = __import__("downloader.browser_media_resolver", fromlist=["resolve"])
    browser_handoff = __import__("downloader.browser_download_handoff", fromlist=["resolve_to_file"])
    cobalt_resolver = __import__("downloader.cobalt_resolver", fromlist=["resolve"])

    browser_candidate_cache = {}

    def _is_local_file(value, temp_dir):
        if not isinstance(value, (str, os.PathLike)) or not temp_dir:
            return False
        try:
            candidate = os.path.realpath(os.fspath(value))
            root = os.path.realpath(temp_dir) + os.sep
            return candidate.startswith(root) and os.path.isfile(candidate) and os.path.getsize(candidate) > 0
        except OSError:
            return False

    def _candidate_from_value(value):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
        return None

    def _queue_candidates(source_url, values):
        if not source_url:
            return
        normalized = []
        for item in values or []:
            candidate = item.get("url") if isinstance(item, dict) else item
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")) and candidate not in normalized:
                normalized.append(candidate)
        if normalized:
            browser_candidate_cache[source_url] = normalized[:16]

    async def wrapped(url, *args, **kwargs):
        print("🔎 Smart Media Bridge: entered", flush=True)
        try:
            provider_resolved = await asyncio.to_thread(
                shahid4u_resolver.resolve,
                url,
                validator=bot_module.validate_public_http_url,
                request_factory=bot_module.Request,
                open_function=bot_module.safe_urlopen,
                read_function=bot_module.read_limited,
            )
        except Exception as exc:
            print(f"⚠️ Shahid4u Resolver failed: {type(exc).__name__}", flush=True)
            provider_resolved = []
        if provider_resolved:
            _queue_candidates(url, provider_resolved)
            print(f"🎯 Shahid4u Provider: queued {len(provider_resolved)} candidate(s) for Browser Download Handoff", flush=True)
            return provider_resolved

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
            resolved = await asyncio.to_thread(
                resolver.resolve,
                url,
                validator=bot_module.validate_public_http_url,
                request_factory=bot_module.Request,
                open_function=bot_module.safe_urlopen,
                read_function=bot_module.read_limited,
            )
        except Exception as exc:
            print(f"⚠️ Smart Search Resolver failed: {type(exc).__name__}", flush=True)
            resolved = []
        if resolved:
            print(f"🔎 Smart Search Resolver: resolved {len(resolved)} public media candidate(s)", flush=True)
            return resolved

        try:
            print("🌐 Smart Media Bridge: browser resolver starting", flush=True)
            browser_resolved = await asyncio.to_thread(
                browser_resolver.resolve,
                url,
                validator=bot_module.validate_public_http_url,
            )
        except Exception as exc:
            print(f"⚠️ Browser Media Resolver failed: {type(exc).__name__}", flush=True)
            browser_resolved = []
        if browser_resolved:
            _queue_candidates(url, browser_resolved)
            print(f"🌐 Browser Media Resolver: resolved {len(browser_resolved)} public media candidate(s); queued for Browser Download Handoff", flush=True)
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

            temp_dir = kwargs.get("temp_dir")
            is_audio = bool(kwargs.get("is_audio", False))
            diagnostics = result[3] if isinstance(result, tuple) and len(result) > 3 else {}
            source_url = kwargs.get("url")
            if source_url is None and args:
                source_url = args[0]

            if isinstance(result, tuple) and result and _is_local_file(result[0], temp_dir):
                browser_candidate_cache.pop(source_url, None)
                return result

            candidates = diagnostics.get("candidates", []) if isinstance(diagnostics, dict) else []
            candidate_urls = []
            cached_candidates = browser_candidate_cache.pop(source_url, []) if source_url else []
            for item in cached_candidates:
                candidate = item.get("url") if isinstance(item, dict) else item
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://")) and candidate not in candidate_urls:
                    candidate_urls.append(candidate)

            if isinstance(result, tuple) and result:
                direct_candidate = _candidate_from_value(result[0])
                if direct_candidate and direct_candidate not in candidate_urls:
                    candidate_urls.append(direct_candidate)

            if isinstance(candidates, (list, tuple)):
                for item in candidates:
                    candidate = item.get("url") if isinstance(item, dict) else item
                    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")) and candidate not in candidate_urls:
                        candidate_urls.append(candidate)

            if not temp_dir or not candidate_urls:
                return result

            print(f"🌐 Browser Download Handoff: normal direct download produced no file; processing {len(candidate_urls)} candidate(s)", flush=True)
            max_bytes = getattr(bot_module, "MAX_AUDIO_DOWNLOAD_BYTES" if is_audio else "MAX_VIDEO_DOWNLOAD_BYTES", 500 * 1024 * 1024)
            source_host = ""
            try:
                source_host = (bot_module.urlparse(source_url).hostname or "").lower().rstrip(".")
            except Exception:
                pass
            strict_provider_validation = source_host == "shahid4u.run" or source_host.endswith(".shahid4u.run")
            handoff_kwargs = {
                "validator": bot_module.validate_public_http_url,
                "is_audio": is_audio,
                "max_file_bytes": max_bytes,
                "referer_url": source_url if isinstance(source_url, str) else None,
            }
            if strict_provider_validation and not is_audio:
                handoff_kwargs.update({
                    "min_video_bytes": 5 * 1024 * 1024,
                    "min_video_duration": 60.0,
                })
                print("🎯 Shahid4u Handoff: strict media validation enabled (>=5MB and >=60s)", flush=True)

            for candidate in candidate_urls[:12]:
                try:
                    print(f"🌐 Browser Download Handoff: trying candidate {candidate.split('?', 1)[0]}", flush=True)
                    local_path = await asyncio.to_thread(
                        browser_handoff.resolve_to_file,
                        candidate,
                        temp_dir,
                        timeout_ms=45_000,
                        settle_ms=2_000,
                        **handoff_kwargs,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"⚠️ Browser Download Handoff failed: {type(exc).__name__}", flush=True)
                    local_path = None
                if _is_local_file(local_path, temp_dir):
                    try:
                        size = os.path.getsize(local_path)
                    except OSError:
                        size = 0
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

            temp_dir = kwargs.get("temp_dir")
            if isinstance(smart_result, tuple) and smart_result and _is_local_file(smart_result[0], temp_dir):
                return smart_result

            url = kwargs.get("url")
            if url is None and args:
                url = args[0]
            if not url:
                return smart_result

            print("🌐 Smart Media Bridge: handing failed Smart Extraction to direct-media chain", flush=True)
            fallback_kwargs = {
                "url": url,
                "temp_dir": temp_dir,
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
                if isinstance(return_value, tuple) and return_value and _is_local_file(return_value[0], temp_dir):
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
    print("🎯 Shahid4u Provider Resolver: ENABLED", flush=True)
