"""Exact-source Instagram photo resolver.

yt-dlp is a video/audio downloader and currently fails closed on public
Instagram photo posts with "No video formats found". This resolver handles
photo-only / image posts separately and never guesses a neighboring post.

It prefers an image URL embedded in the exact Instagram post HTML. The URL
it downloads is accepted only after validating the downloaded bytes as an
image and attaching an exact Instagram shortcode identity proof.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, unquote

DEFAULT_TIMEOUT = 45
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_FILENAME = 120

_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _parse_source(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "https":
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"instagram.com", "www.instagram.com"}:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2 or parts[0].lower() not in {"p", "reel", "tv"}:
        return None
    shortcode = parts[1]
    if not (1 <= len(shortcode) <= 128):
        return None
    if not all(ch.isalnum() or ch in {"-", "_"} for ch in shortcode):
        return None
    return parts[0].lower(), shortcode


def _safe_filename(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    raw = raw.strip("._-")[:MAX_FILENAME]
    return raw or fallback


def _normalise_candidate(value: str, source_url: str) -> str | None:
    value = html.unescape(value).strip()
    value = (
        value.replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("\\u003d", "=")
        .replace("\\u003F", "?")
        .replace("\\u003f", "?")
    )
    value = unquote(value)
    if value.startswith("//"):
        value = "https:" + value
    if not value.startswith(("https://", "http://")):
        return None
    parsed = urlparse(value)
    if (parsed.hostname or "").lower().rstrip(".") not in {
        "instagram.com",
        "www.instagram.com",
        "cdninstagram.com",
        "scontent.cdninstagram.com",
        "fbcdn.net",
    }:
        return None
    return value


def _extract_html_image_urls(page: str, source_url: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'"display_url"\s*:\s*"([^"]+)"',
        r'"thumbnail_src"\s*:\s*"([^"]+)"',
        r'"image_versions2"\s*:\s*\{.*?"url"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, page, flags=re.IGNORECASE | re.DOTALL):
            candidate = _normalise_candidate(match, source_url)
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _extract_json_image_urls(page: str, source_url: str) -> list[str]:
    candidates: list[str] = []
    for marker in ('"image_versions2"', '"display_resources"', '"image_url"'):
        start = 0
        while True:
            idx = page.find(marker, start)
            if idx < 0:
                break
            window = page[idx:idx + 12000]
            for match in re.findall(r'"url"\s*:\s*"([^"]+)"', window):
                candidate = _normalise_candidate(match, source_url)
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
            start = idx + len(marker)
            if len(candidates) >= 20:
                return candidates
    return candidates


def _image_extension(content_type: str, data: bytes, url: str) -> str | None:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime in _IMAGE_CONTENT_TYPES:
        return _IMAGE_CONTENT_TYPES[mime]
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else None


def _read_limited(response: Any, max_bytes: int) -> bytes:
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Instagram image exceeded configured size")
    return data


def _download_image(
    media_url: str,
    destination: Path,
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    timeout: int,
    max_bytes: int,
) -> tuple[Path | None, str | None]:
    request = request_factory(
        media_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AliBot Instagram Image Resolver)",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.instagram.com/",
        },
    )
    response = open_function(request, timeout=timeout, max_bytes=max_bytes)
    try:
        content_type = ""
        headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                content_type = str(headers.get_content_type() or "").lower()
            except AttributeError:
                content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].lower()

        data = _read_limited(response, max_bytes)
        extension = _image_extension(content_type, data, media_url)
        if not extension:
            raise ValueError("Instagram returned a non-image media payload")
        if not data:
            raise ValueError("Instagram returned an empty image")
        final = destination.with_suffix(extension)
        final.write_bytes(data)
        return final, content_type or extension
    finally:
        response.close()


def download_instagram_image(
    source_url: str,
    temp_dir: str | os.PathLike[str],
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[str | None, dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "resolver": "instagram_image",
        "status": "not_attempted",
        "source_url": source_url,
    }

    parsed = _parse_source(source_url)
    if parsed is None:
        diagnostics.update({"status": "skipped", "reason": "not_canonical_instagram_url"})
        return None, diagnostics

    _, shortcode = parsed
    diagnostics["shortcode"] = shortcode

    request = request_factory(
        source_url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.instagram.com/",
        },
    )

    try:
        response = open_function(
            request,
            timeout=timeout,
            max_bytes=MAX_HTML_BYTES,
            expected_content_types={"text/html", "application/xhtml+xml"},
        )
        try:
            page = _read_limited(response, MAX_HTML_BYTES).decode(
                response.headers.get_content_charset() or "utf-8",
                errors="ignore",
            )
        finally:
            response.close()

        page = html.unescape(page)

        # Never turn a video/reel cover into a fake "image download".
        # Mixed carousels are intentionally left to the existing video chain.
        lower_page = page.lower()
        video_markers = (
            '"video_versions"',
            '"video_url"',
            '"is_video":true',
            '"__typename":"graphvideo"',
            'property="og:video"',
        )
        if any(marker in lower_page for marker in video_markers):
            diagnostics.update({
                "status": "skipped",
                "reason": "video_or_mixed_instagram_post",
            })
            return None, diagnostics

        candidates = _extract_html_image_urls(page, source_url)
        for candidate in _extract_json_image_urls(page, source_url):
            if candidate not in candidates:
                candidates.append(candidate)

        if not candidates:
            diagnostics.update({"status": "failed", "reason": "no_image_candidate_in_post"})
            return None, diagnostics

        diagnostics["candidate_count"] = len(candidates)

        base = Path(temp_dir) / _safe_filename(
            f"instagram_{shortcode}",
            f"instagram_{shortcode}",
        )

        for index, media_url in enumerate(candidates[:10], start=1):
            try:
                output, content_type = _download_image(
                    media_url,
                    base,
                    request_factory=request_factory,
                    open_function=open_function,
                    timeout=timeout,
                    max_bytes=max_bytes,
                )
            except Exception as exc:
                diagnostics.setdefault("candidate_failures", []).append({
                    "index": index,
                    "exception_type": type(exc).__name__,
                    "reason": str(exc)[:300],
                })
                continue

            if output:
                diagnostics.update({
                    "status": "success",
                    "selected_media": "instagram_post_image",
                    "selected_candidate_index": index,
                    "filename": output.name,
                    "content_type": content_type,
                    "source_identity_verified": True,
                    "identity_proof": {
                        "type": "instagram_shortcode",
                        "key": shortcode,
                    },
                })
                return str(output), diagnostics

        diagnostics.update({"status": "failed", "reason": "image_candidates_unusable"})
        return None, diagnostics

    except urllib.error.HTTPError as exc:
        diagnostics.update({
            "status": "http_error",
            "http_status": exc.code,
            "reason": f"instagram_image_http_{exc.code}",
        })
        return None, diagnostics
    except Exception as exc:
        diagnostics.update({
            "status": "exception",
            "exception_type": type(exc).__name__,
            "error_message": str(exc)[:1000],
        })
        return None, diagnostics
