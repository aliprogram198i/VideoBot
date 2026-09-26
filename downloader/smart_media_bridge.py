"""Async-safe bridge between the legacy media extractor and Smart Search."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
from urllib.parse import urlparse

SHAHID4U_MAX_HANDOFF_BYTES = 2 * 1024 * 1024 * 1024
GENERIC_MAX_VIDEO_HANDOFF_BYTES = 2 * 1024 * 1024 * 1024
MOVIE_MIN_VIDEO_BYTES = 5 * 1024 * 1024
MOVIE_MIN_VIDEO_DURATION = 60.0
SHAHID4U_MIN_VIDEO_BYTES = 5 * 1024 * 1024
SHAHID4U_MIN_VIDEO_DURATION = 60.0
SHAHID4U_PREFLIGHT_READ_BYTES = 64 * 1024
QUALITY_RE = re.compile(r"(?<!\d)(2160|1440|1080|720|480|360|240)\s*p?\b", re.I)

_CONTEXT_PLATFORMS = {
    "youtube": ("youtube.com", "youtu.be"),
    "instagram": ("instagram.com", "instagr.am"),
    "facebook": ("facebook.com", "fb.watch"),
    "tiktok": ("tiktok.com", "tiktokcdn.com"),
    "twitter": ("twitter.com", "x.com"),
    "reddit": ("reddit.com", "redd.it"),
    "shahid4u": ("shahid4u.run", "shhaiid4u.net", "shahid4u.net"),
    "telegram": ("t.me", "telegram.me"),
    "vimeo": ("vimeo.com",),
    "dailymotion": ("dailymotion.com", "dai.ly"),
}


def _resolver_context(source_url, media_kind="unknown"):
    try:
        host = (urlparse(str(source_url)).hostname or "").lower().rstrip(".")
    except Exception:
        host = ""
    platform = "unknown"
    for name, suffixes in _CONTEXT_PLATFORMS.items():
        if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            platform = name
            break
    kind = str(media_kind or "unknown").lower()
    if kind not in {"hls", "dash", "progressive", "iframe", "unknown"}:
        kind = "unknown"
    return platform, kind


def _telegram_embed_urls(url):
    """Build deterministic Telegram public/embed variants for one post."""
    try:
        from .telegram_identity import parse_telegram_post_url
        identity = parse_telegram_post_url(url)
    except Exception:
        return []
    if identity is None:
        return []
    channel = identity.channel
    message_id = identity.message_id
    base_urls = [
        f"https://t.me/{channel}/{message_id}",
        f"https://t.me/s/{channel}/{message_id}",
    ]
    variants = []
    # Telegram's own web extractor uses embed=1&single=1.  The
    # single flag is important: it constrains the returned HTML to the
    # requested message instead of a surrounding channel timeline.
    for base in base_urls:
        for query in (
            "embed=1&single=1",
            "embed=1&single=1&mode=tme",
            "embed=1&mode=tme",
            "embed=1",
        ):
            candidate = f"{base}?{query}"
            if candidate not in variants:
                variants.append(candidate)
    return variants


def _facebook_embed_urls(url, *, resolved_id=None):
    """Build deterministic Facebook embed variants for public video/reel URLs."""
    if not isinstance(url, str) or not url.strip():
        return []

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return []

    parts = [part for part in parsed.path.split('/') if part]
    if len(parts) == 2 and parts[0].lower() in {"reel", "videos"}:
        video_id = parts[1]
    elif parsed.path.rstrip("/").lower() == "/watch":
        from urllib.parse import parse_qs
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
    elif len(parts) == 3 and parts[0].lower() == "share" and parts[1].lower() == "r":
        video_id = ""
    else:
        video_id = ""

    if resolved_id:
        if not isinstance(resolved_id, str) or not re.fullmatch(r"\d{5,30}", resolved_id):
            return []
        video_id = resolved_id

    if video_id and not re.fullmatch(r"\d{5,30}", video_id):
        return []

    is_share_r = len(parts) == 3 and parts[0].lower() == "share" and parts[1].lower() == "r"
    if not video_id and not is_share_r:
        return []

    from urllib.parse import quote
    encoded_source = quote(url.strip(), safe='')
    variants = []
    if is_share_r and not video_id:
        variants.append(
            (
                "https://www.facebook.com/plugins/video.php"
                f"?href={encoded_source}&show_text=false&width=560"
            )
        )
    elif not is_share_r:
        variants.append(
            (
                "https://www.facebook.com/plugins/video.php"
                f"?href={encoded_source}&show_text=false&width=560"
            )
        )
    if video_id:
        variants.append(
            (
                "https://www.facebook.com/plugins/video.php"
                f"?href=https%3A%2F%2Fwww.facebook.com%2Fwatch%2F%3Fv%3D{video_id}"
                "&show_text=false&width=560"
            )
        )
    return list(dict.fromkeys(variants))


def _facebook_extract_canonical_id(value):
    """Extract a numeric Facebook reel/video ID from a URL or bounded HTML."""
    if not isinstance(value, str) or not value:
        return None

    import html
    text = html.unescape(value).replace("\\/", "/")
    candidates = [text]
    candidates.extend(
        re.findall(
            r"https?://(?:www\.)?(?:m\.)?facebook\.com/[^\s'<>]+",
            text,
            re.I,
        )
    )
    for candidate_url in candidates:
        try:
            parsed = urlparse(candidate_url)
        except Exception:
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
            continue
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0].lower() in {"reel", "videos"}:
            candidate = parts[1]
        elif parsed.path.rstrip("/").lower() == "/watch":
            from urllib.parse import parse_qs
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            continue
        if re.fullmatch(r"\d{5,30}", candidate):
            return candidate

    # Facebook's share/r HTML can expose the canonical resource only inside
    # bounded bootstrap JSON rather than a canonical/reel URL. This is still
    # a source-page identity signal, so accept only explicit video-id fields
    # and only when the value is a numeric Facebook media identifier.
    for pattern in (
        r"""["']video_id["']\\s*[:=]\\s*["'](\\d{5,30})["']""",
        r"""["']videoId["']\\s*[:=]\\s*["'](\\d{5,30})["']""",
        r"""["']videoID["']\\s*[:=]\\s*["'](\\d{5,30})["']""",
        r"""\\bdata-video-id\\s*=\\s*["'](\\d{5,30})["']""",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def _facebook_resolve_share_id(bot_module, url):
    """Resolve a Facebook share/r URL to its canonical numeric resource ID."""
    if not isinstance(url, str) or not url.strip():
        return None

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return None
    if not (len(parts) == 3 and parts[0].lower() == "share" and parts[1].lower() == "r"):
        return None

    try:
        request = bot_module.Request(
            url.strip(),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 10; K) "
                    "AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with bot_module.safe_urlopen(
            request,
            timeout=12,
            max_bytes=512 * 1024,
        ) as response:
            geturl = getattr(response, "geturl", None)
            final_url = geturl() if callable(geturl) else ""
            final = final_url if isinstance(final_url, str) else ""
            candidate = _facebook_extract_canonical_id(final)
            if candidate:
                return candidate

            try:
                raw = response.read(512 * 1024)
            except Exception:
                raw = b""
            if isinstance(raw, bytes):
                body = raw.decode("utf-8", errors="replace")
            else:
                body = raw if isinstance(raw, str) else ""
            return _facebook_extract_canonical_id(body)
    except Exception:
        return None


def _protected_social_post(url):
    """Return True for public Telegram/Instagram post URLs protected by source identity gates."""
    try:
        host = (urlparse(str(url)).hostname or "").lower().rstrip(".")
        if host in {"t.me", "telegram.me"} or host.endswith(".t.me") or host.endswith(".telegram.me"):
            from .telegram_identity import parse_telegram_post_url
            # Protected hosts remain protected even when the URL is not a
            # parseable public post. This prevents generic resolver fallback.
            parse_telegram_post_url(url)
            return True
        if host == "instagram.com" or host.endswith(".instagram.com"):
            from .instagram_identity import parse_instagram_post_url
            # Same fail-closed policy for Instagram protected hosts.
            parse_instagram_post_url(url)
            return True
    except Exception:
        # A source-identity check must fail closed for these protected hosts.
        return True
    return False


def _quality_from_value(value):
    if not isinstance(value, str):
        return None
    match = QUALITY_RE.search(value)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _requested_quality(format_option):
    quality = _quality_from_value(str(format_option or ""))
    return quality if quality in {2160, 1440, 1080, 720, 480, 360, 240} else None


def _order_shahid_candidates(values, requested_quality):
    rows = []
    for index, item in enumerate(values or []):
        candidate = item.get("url") if isinstance(item, dict) else item
        if not isinstance(candidate, str) or not candidate.startswith(("http://", "https://")):
            continue
        quality = item.get("quality") if isinstance(item, dict) else None
        if not isinstance(quality, int):
            quality = _quality_from_value(candidate)
        if requested_quality is None:
            bucket = 0 if quality is not None else 1
            distance = -(quality or 0)
        elif quality == requested_quality:
            bucket, distance = 0, 0
        elif quality is not None and quality < requested_quality:
            bucket, distance = 1, requested_quality - quality
        elif quality is None:
            bucket, distance = 2, 0
        else:
            bucket, distance = 3, quality - requested_quality
        score = -(int(item.get("score", 0)) if isinstance(item, dict) else 0)
        rows.append((bucket, distance, score, index, item))
    rows.sort(key=lambda row: row[:4])
    return [row[4] for row in rows]


def _preflight_candidate(bot_module, candidate_url, *, max_bytes, referer_url=None):
    """Prove a candidate is not oversized without downloading its full body."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36",
        "Accept": "*/*",
    }
    if referer_url:
        headers["Referer"] = referer_url
    try:
        request = bot_module.Request(candidate_url, headers=headers, method="HEAD")
        with bot_module.safe_urlopen(request, timeout=12, max_bytes=max_bytes) as response:
            raw = response.headers.get("Content-Length")
            if raw:
                size = int(raw)
                if size > max_bytes:
                    return False, size, "content_length_exceeds_limit"
                return True, size, "content_length"
    except Exception as exc:
        if "exceeds configured size limit" in str(exc).lower():
            return False, max_bytes + 1, "head_response_exceeds_limit"
    try:
        range_headers = dict(headers)
        range_headers["Range"] = "bytes=0-0"
        request = bot_module.Request(candidate_url, headers=range_headers, method="GET")
        with bot_module.safe_urlopen(request, timeout=12, max_bytes=SHAHID4U_PREFLIGHT_READ_BYTES) as response:
            content_range = response.headers.get("Content-Range") or ""
            match = re.search(r"bytes\s+\d+-\d+/(\d+|\*)", content_range, re.I)
            if match and match.group(1) != "*":
                size = int(match.group(1))
                if size > max_bytes:
                    return False, size, "content_range_exceeds_limit"
                return True, size, "content_range"
            raw = response.headers.get("Content-Length")
            if raw and int(raw) > max_bytes:
                return False, int(raw), "range_content_length_exceeds_limit"
    except Exception as exc:
        if "exceeds configured size limit" in str(exc).lower():
            return False, max_bytes + 1, "range_response_exceeds_limit"
    return True, None, "size_unknown"


def install(bot_module) -> None:
    """Compose legacy, provider-specific, static, browser, and Cobalt fallbacks."""
    original = getattr(bot_module, "extract_direct_media_urls", None)
    original_smart = getattr(bot_module, "download_with_smart_extraction", None)
    original_fallback = getattr(bot_module, "download_with_fallback", None)
    if not callable(original) or getattr(original, "_smart_search_bridge", False):
        return
    resolver = __import__("downloader.smart_media_resolver", fromlist=["resolve"])
    shahid4u_resolver = __import__("downloader.shahid4u_resolver", fromlist=["resolve"])
    krx18_resolver = __import__("downloader.krx18_resolver", fromlist=["resolve_media", "is_krx18_url"])
    browser_resolver = __import__("downloader.browser_media_resolver", fromlist=["resolve"])
    browser_handoff = __import__("downloader.browser_download_handoff", fromlist=["resolve_to_file"])
    cobalt_resolver = __import__("downloader.cobalt_resolver", fromlist=["resolve"])
    telemetry_module = __import__("downloader.telemetry", fromlist=["TelemetryContext", "TelemetryRecorder"])
    contracts = __import__("downloader.resolver_contracts", fromlist=["ResolverResult", "result_from_exception"])
    telemetry = telemetry_module.TelemetryRecorder()
    movie_guard = __import__("downloader.movie_source_guard", fromlist=["should_guard", "is_ad_host", "assess_local_media"])
    browser_candidate_cache = {}

    async def _run_resolver(name, operation, *args, source_url=None, media_kind="unknown", **kwargs):
        started = time.monotonic()
        platform, kind = _resolver_context(source_url, media_kind)
        try:
            result = operation(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            contract = contracts.ResolverResult.from_output(
                name,
                result,
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
            telemetry.record_resolver(
                contract,
                context=telemetry_module.TelemetryContext(
                    platform=platform,
                    media_kind=kind,
                ),
            )
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            contract = contracts.result_from_exception(
                name,
                exc,
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
            telemetry.record_resolver(
                contract,
                context=telemetry_module.TelemetryContext(
                    platform=platform,
                    media_kind=kind,
                ),
            )
            raise

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

    def _post_handoff_gate(path, source_url, candidate_url, is_audio):
        if not path or is_audio:
            return True
        try:
            if movie_guard.is_ad_host(candidate_url):
                print("🛡️ Browser candidate gate: rejected advertising host", flush=True)
                os.remove(path)
                return False
        except Exception:
            pass
        try:
            if movie_guard.should_guard(source_url, is_audio=False):
                gate = movie_guard.assess_local_media(
                    path,
                    source_url,
                    candidate_url=candidate_url,
                    is_audio=False,
                    min_bytes=MOVIE_MIN_VIDEO_BYTES,
                    min_duration=MOVIE_MIN_VIDEO_DURATION,
                )
                if not gate.accepted:
                    print(f"🛡️ Browser candidate gate: rejected media ({gate.reason})", flush=True)
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    return False
                print(f"🛡️ Browser candidate gate: accepted verified media ({gate.duration_seconds:.1f}s, {gate.size_bytes} bytes)", flush=True)
        except Exception as exc:
            print(f"⚠️ Browser candidate gate failed closed ({type(exc).__name__})", flush=True)
            try:
                os.remove(path)
            except OSError:
                pass
            return False
        return True

    def _queue_candidates(source_url, values):
        if not source_url:
            return
        normalized = []
        for item in values or []:
            candidate = item.get("url") if isinstance(item, dict) else item
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")) and not any(
                (existing.get("url") if isinstance(existing, dict) else existing) == candidate
                for existing in normalized
            ):
                normalized.append(item if isinstance(item, dict) else candidate)
        if normalized:
            browser_candidate_cache[source_url] = normalized[:16]

    async def wrapped(url, *args, **kwargs):
        print("🔎 Smart Media Bridge: entered", flush=True)
        context = {"source_url": url, "media_kind": "unknown"}
        if krx18_resolver.is_krx18_url(url):
            print("🎯 KRX18 Dedicated Resolver: public-source path", flush=True)
            try:
                krx_candidates = await _run_resolver(
                    "krx18_public",
                    krx18_resolver.resolve_media,
                    url,
                    validator=bot_module.validate_public_http_url,
                    request_factory=bot_module.Request,
                    open_function=bot_module.safe_urlopen,
                    read_function=bot_module.read_limited,
                    source_url=url,
                    media_kind="iframe",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ KRX18 Dedicated Resolver failed: {type(exc).__name__}", flush=True)
                krx_candidates = []
            if krx_candidates:
                _queue_candidates(url, krx_candidates)
                print(f"✅ KRX18 Dedicated Resolver: {len(krx_candidates)} verified candidate(s)", flush=True)
                return krx_candidates
            print("🛡️ KRX18 Dedicated Resolver: no verified public media; fail-closed", flush=True)
            return []

        try:
            resolve_candidates = getattr(shahid4u_resolver, "resolve_candidates", None)
            if callable(resolve_candidates):
                provider_resolved = await _run_resolver("shahid4u", resolve_candidates, url,
                    validator=bot_module.validate_public_http_url,
                    request_factory=bot_module.Request,
                    open_function=bot_module.safe_urlopen,
                    read_function=bot_module.read_limited, **context)
                provider_urls = [item.get("url") for item in provider_resolved if isinstance(item, dict) and isinstance(item.get("url"), str)]
            else:
                provider_resolved = await _run_resolver("shahid4u", shahid4u_resolver.resolve, url,
                    validator=bot_module.validate_public_http_url,
                    request_factory=bot_module.Request,
                    open_function=bot_module.safe_urlopen,
                    read_function=bot_module.read_limited, **context)
                provider_urls = provider_resolved
        except Exception as exc:
            print(f"⚠️ Shahid4u Resolver failed: {type(exc).__name__}", flush=True)
            provider_resolved = []
            provider_urls = []
        if provider_urls:
            _queue_candidates(url, provider_resolved)
            print(f"🎯 Shahid4u Provider: queued {len(provider_urls)} candidate(s) for Browser Download Handoff", flush=True)
            return provider_urls
        try:
            existing = await _run_resolver("legacy_extractor", original, url, *args, source_url=url, **kwargs)
        except Exception as exc:
            print(f"⚠️ Smart Search legacy extractor failed: {type(exc).__name__}", flush=True)
            existing = []
        if existing:
            print(f"🔎 Smart Media Bridge: legacy returned {len(existing)} candidate(s)", flush=True)
            return existing
        try:
            print("🔎 Smart Media Bridge: static resolver starting", flush=True)
            resolved = await _run_resolver("smart_media", resolver.resolve, url,
                validator=bot_module.validate_public_http_url, request_factory=bot_module.Request,
                open_function=bot_module.safe_urlopen, read_function=bot_module.read_limited,
                source_url=url, media_kind="unknown")
        except Exception as exc:
            print(f"⚠️ Smart Search Resolver failed: {type(exc).__name__}", flush=True)
            resolved = []
        if resolved:
            print(f"🔎 Smart Search Resolver: resolved {len(resolved)} public media candidate(s)", flush=True)
            return resolved
        try:
            print("🌐 Smart Media Bridge: browser resolver starting", flush=True)
            browser_resolved = await _run_resolver("browser_media", browser_resolver.resolve, url,
                validator=bot_module.validate_public_http_url, source_url=url, media_kind="iframe")
        except Exception as exc:
            print(f"⚠️ Browser Media Resolver failed: {type(exc).__name__}", flush=True)
            browser_resolved = []
        if browser_resolved:
            _queue_candidates(url, browser_resolved)
            print(f"🌐 Browser Media Resolver: resolved {len(browser_resolved)} public media candidate(s); queued for Browser Download Handoff", flush=True)
            return browser_resolved
        try:
            print("🧩 Smart Media Bridge: Cobalt resolver starting", flush=True)
            cobalt_resolved = await _run_resolver("cobalt", cobalt_resolver.resolve, url, source_url=url, media_kind="unknown")
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
            format_option = kwargs.get("format_option")
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
            is_shahid4u = False
            try:
                source_host = (urlparse(str(source_url)).hostname or "").lower().rstrip(".")
                is_shahid4u = source_host == "shahid4u.run" or source_host.endswith(".shahid4u.run")
            except Exception:
                pass
            if is_shahid4u and not is_audio:
                requested_quality = _requested_quality(format_option)
                cached_candidates = _order_shahid_candidates(cached_candidates, requested_quality)
                print(f"🎚 Shahid4u quality target: {requested_quality or 'best available'}", flush=True)
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
            if _protected_social_post(source_url):
                print(
                    "🛡️ Browser Download Handoff: blocked generic handoff for protected social post identity",
                    flush=True,
                )
                return result
            print(f"🌐 Browser Download Handoff: normal direct download produced no file; processing {len(candidate_urls)} candidate(s)", flush=True)
            max_bytes = getattr(bot_module, "MAX_AUDIO_DOWNLOAD_BYTES" if is_audio else "MAX_VIDEO_DOWNLOAD_BYTES", 500 * 1024 * 1024)
            if not is_audio:
                max_bytes = max(max_bytes, GENERIC_MAX_VIDEO_HANDOFF_BYTES)
            if is_shahid4u and not is_audio:
                max_bytes = max(max_bytes, SHAHID4U_MAX_HANDOFF_BYTES)
            handoff_kwargs = {"validator": bot_module.validate_public_http_url, "is_audio": is_audio, "max_file_bytes": max_bytes, "referer_url": source_url if isinstance(source_url, str) else None}
            if is_shahid4u and not is_audio:
                handoff_kwargs.update({"min_video_bytes": SHAHID4U_MIN_VIDEO_BYTES, "min_video_duration": SHAHID4U_MIN_VIDEO_DURATION})
                print("🎯 Shahid4u Handoff: strict media validation enabled (>=5MB, >=60s, max 2GB)", flush=True)
            for candidate in candidate_urls[:12]:
                if is_shahid4u and not is_audio:
                    allowed, size, reason = await asyncio.to_thread(_preflight_candidate, bot_module, candidate,
                        max_bytes=max_bytes, referer_url=source_url if isinstance(source_url, str) else None)
                    if not allowed:
                        print(f"🛑 Shahid4u preflight rejected candidate ({reason}, size={size})", flush=True)
                        continue
                    print(f"✅ Shahid4u preflight accepted candidate ({reason}, size={size if size is not None else 'unknown'})", flush=True)
                try:
                    print(f"🌐 Browser Download Handoff: trying candidate {candidate.split('?', 1)[0]}", flush=True)
                    local_path = await asyncio.to_thread(browser_handoff.resolve_to_file, candidate, temp_dir,
                        timeout_ms=45_000, settle_ms=2_000, **handoff_kwargs)
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
                    if not _post_handoff_gate(local_path, source_url, candidate, is_audio):
                        print(f"🛡️ Browser Download Handoff: candidate rejected after download ({size} bytes); trying next candidate", flush=True)
                        continue
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

            # Telegram public post pages can expose the actual media only through
            # their documented embed rendering. Try those deterministic variants
            # before failing closed; each variant still passes the exact post
            # identity gate inside the original Smart Extraction implementation.
            telegram_variants = _telegram_embed_urls(url)
            if telegram_variants:
                print(
                    f"📨 Telegram Source Resolver: trying {len(telegram_variants)} public/embed variant(s)",
                    flush=True,
                )
                for telegram_url in telegram_variants:
                    try:
                        variant_kwargs = dict(kwargs)
                        if args:
                            variant_args = list(args)
                            variant_args[0] = telegram_url
                            variant_kwargs.pop("url", None)
                        else:
                            variant_args = []
                            variant_kwargs["url"] = telegram_url
                        variant_result = original_smart(*variant_args, **variant_kwargs)
                        if inspect.isawaitable(variant_result):
                            variant_result = await variant_result
                        if isinstance(variant_result, tuple) and variant_result and _is_local_file(variant_result[0], temp_dir):
                            print(
                                "✅ Telegram Source Resolver: exact-post media extracted",
                                flush=True,
                            )
                            return variant_result
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        print(
                            f"⚠️ Telegram Source Resolver variant failed: {type(exc).__name__}",
                            flush=True,
                        )

            # Facebook Reels currently fail in the upstream yt-dlp extractor with
            # "Cannot parse data" on public Reel URLs. Use the official Facebook
            # video plugin as a deterministic source-page variant before generic
            # fallbacks. The embed href remains the exact user-supplied URL.
            facebook_resolved_id = _facebook_resolve_share_id(bot_module, url)
            facebook_variants = _facebook_embed_urls(
                url,
                resolved_id=facebook_resolved_id,
            )
            if facebook_resolved_id:
                print(
                    f"📘 Facebook Source Resolver: share/r resolved to canonical video id={facebook_resolved_id}",
                    flush=True,
                )
            if facebook_variants:
                print(
                    f"📘 Facebook Source Resolver: trying {len(facebook_variants)} official embed variant(s)",
                    flush=True,
                )
                for facebook_url in facebook_variants:
                    try:
                        variant_kwargs = dict(kwargs)
                        if args:
                            variant_args = list(args)
                            variant_args[0] = facebook_url
                            variant_kwargs.pop("url", None)
                        else:
                            variant_args = []
                            variant_kwargs["url"] = facebook_url
                        variant_result = original_smart(*variant_args, **variant_kwargs)
                        if inspect.isawaitable(variant_result):
                            variant_result = await variant_result
                        if isinstance(variant_result, tuple) and variant_result and _is_local_file(variant_result[0], temp_dir):
                            print(
                                "✅ Facebook Source Resolver: official embed media extracted",
                                flush=True,
                            )
                            return variant_result
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        print(f"⚠️ Facebook Source Resolver variant failed: {type(exc).__name__}", flush=True)

            if _protected_social_post(url):
                print(
                    "🛡️ Smart Media Bridge: blocked generic direct-media handoff for protected social post identity",
                    flush=True,
                )
                return smart_result
            print("🌐 Smart Media Bridge: handing failed Smart Extraction to direct-media chain", flush=True)
            fallback_kwargs = {"url": url, "temp_dir": temp_dir, "output_template": kwargs.get("output_template"), "format_option": kwargs.get("format_option"), "is_audio": kwargs.get("is_audio", False), "attempt_id": kwargs.get("attempt_id"), "attempt_number": kwargs.get("attempt_number")}
            try:
                return_value = wrapped_fallback(**fallback_kwargs) if callable(wrapped_fallback) else original_fallback(**fallback_kwargs)
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
    print("📊 Resolver Outcome Telemetry: ENABLED (context-aware)", flush=True)
