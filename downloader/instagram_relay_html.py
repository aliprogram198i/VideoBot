"""Direct Instagram Relay HTML resolver.

Reads the public Instagram page payload embedded in Relay/ScheduledServerJS
scripts. No cookies, credentials, browser sessions, or access-control bypass.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_HTML_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_BYTES = 49 * 1024 * 1024
MAX_FILENAME = 120
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
_RELAY_MARKERS = (
    "xdt_api__v1__clips__home__connection_v2",
    "ScheduledServerJS",
)


def _safe_filename(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")[:MAX_FILENAME]
    return raw or fallback


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
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() not in {"reel", "reels", "p", "tv"}:
        return None
    shortcode = parts[1]
    if not 1 <= len(shortcode) <= 128:
        return None
    if not all(ch.isalnum() or ch in {"_", "-"} for ch in shortcode):
        return None
    return parts[0].lower(), shortcode


def _decode_script(text: str) -> str:
    value = html.unescape(text).replace("\\/", "/")
    return value


def _json_value_after(text: str, marker: str, start: int) -> Any | None:
    index = text.find(marker, start)
    if index < 0:
        return None
    colon = text.find(":", index + len(marker))
    if colon < 0:
        return None
    payload = text[colon + 1 :].lstrip()
    try:
        return json.JSONDecoder().raw_decode(payload)[0]
    except (TypeError, ValueError):
        return None


def _candidate_item_starts(text: str, shortcode: str) -> list[int]:
    needles = (
        f'"code":"{shortcode}"',
        f'"shortcode":"{shortcode}"',
        f'"code": "{shortcode}"',
        f'"shortcode": "{shortcode}"',
    )
    starts = []
    for needle in needles:
        offset = 0
        while True:
            found = text.find(needle, offset)
            if found < 0:
                break
            starts.append(found)
            offset = found + len(needle)
    return sorted(set(starts))


def _extract_video_urls_from_script(
    script: str,
    shortcode: str,
) -> tuple[list[str], dict[str, Any]] | None:
    text = _decode_script(script)
    if not any(marker in text for marker in _RELAY_MARKERS) and "video_versions" not in text:
        return None

    for start in _candidate_item_starts(text, shortcode):
        carousel = _json_value_after(text, '"carousel_media"', start)
        if isinstance(carousel, list):
            candidates: list[str] = []
            for child in carousel:
                if not isinstance(child, dict) or child.get("media_type") != 2:
                    continue
                versions = child.get("video_versions")
                if not isinstance(versions, list):
                    continue
                child_urls = [
                    value.get("url")
                    for value in versions
                    if isinstance(value, dict)
                    and isinstance(value.get("url"), str)
                    and value["url"].startswith(("https://", "http://"))
                ]
                if child_urls:
                    candidates.append(child_urls[0])
            if len(candidates) == 1:
                return candidates, {"selection": "single_carousel_video"}
            if len(candidates) > 1:
                return None
            # A carousel with no video is not allowed to fall through to an
            # unrelated video_versions field elsewhere in the parent object.
            if carousel:
                continue

        video_versions = _json_value_after(text, '"video_versions"', start)
        if isinstance(video_versions, list):
            urls = [
                value.get("url")
                for value in video_versions
                if isinstance(value, dict)
                and isinstance(value.get("url"), str)
                and value["url"].startswith(("https://", "http://"))
            ]
            if urls:
                return urls, {"selection": "video_versions"}
    return None


def _extract_media_from_html(html_text: str, shortcode: str) -> tuple[list[str], dict[str, Any]] | None:
    scripts = re.findall(
        r"<script\b[^>]*>(.*?)</script>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for script in scripts:
        found = _extract_video_urls_from_script(script, shortcode)
        if found:
            return found
    return None


def _download_media(
    media_url: str,
    destination: Path,
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    timeout: int,
    max_bytes: int,
    referer: str,
) -> None:
    request = request_factory(
        media_url,
        headers={"User-Agent": _USER_AGENT, "Referer": referer, "Accept": "*/*"},
    )
    response = open_function(request, timeout=timeout, max_bytes=max_bytes)
    try:
        headers = getattr(response, "headers", None)
        content_type = ""
        if headers is not None:
            try:
                content_type = (headers.get_content_type() or "").lower()
            except AttributeError:
                pass
        if content_type and not (
            content_type.startswith("video/")
            or content_type == "application/octet-stream"
        ):
            raise ValueError(f"Unexpected Instagram media content type: {content_type}")
        total = 0
        with destination.open("wb") as output:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Instagram media exceeded configured size")
                output.write(chunk)
        if total <= 0:
            raise ValueError("Instagram returned an empty media file")
    finally:
        response.close()


def download_instagram_with_relay(
    source_url: str,
    temp_dir: str | os.PathLike[str],
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    timeout: int = DEFAULT_TIMEOUT,
    max_html_bytes: int = DEFAULT_MAX_HTML_BYTES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[str | None, dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "resolver": "instagram_relay_html",
        "status": "not_attempted",
        "source_url": source_url,
    }
    parsed = _parse_source(source_url)
    if parsed is None:
        diagnostics.update({"status": "skipped", "reason": "not_canonical_instagram_url"})
        return None, diagnostics

    route, shortcode = parsed
    page_urls = [source_url]
    if route in {"reel", "reels"}:
        page_urls.append(f"https://www.instagram.com/p/{shortcode}/")

    last_reason = "no_relay_video"
    for page_url in dict.fromkeys(page_urls):
        request = request_factory(
            page_url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            response = open_function(
                request,
                timeout=timeout,
                max_bytes=max_html_bytes,
                expected_content_types={"text/html", "application/xhtml+xml"},
            )
        except urllib.error.HTTPError as exc:
            last_reason = f"instagram_page_http_{exc.code}"
            diagnostics["last_http_status"] = exc.code
            continue
        except Exception as exc:
            last_reason = type(exc).__name__
            continue

        try:
            raw = response.read(max_html_bytes + 1)
            if len(raw) > max_html_bytes:
                last_reason = "html_exceeded_configured_size"
                continue
            page_html = raw.decode("utf-8", errors="replace")
        finally:
            response.close()

        extracted = _extract_media_from_html(page_html, shortcode)
        if not extracted:
            last_reason = "no_exact_relay_video"
            continue

        media_urls, selection = extracted
        destination = Path(temp_dir) / _safe_filename(
            f"instagram_{shortcode}",
            f"instagram_{shortcode}.mp4",
        )
        try:
            _download_media(
                media_urls[0],
                destination,
                request_factory=request_factory,
                open_function=open_function,
                timeout=timeout,
                max_bytes=max_bytes,
                referer=f"https://www.instagram.com/reel/{shortcode}/",
            )
        except Exception as exc:
            last_reason = f"media_download_{type(exc).__name__}"
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            continue

        diagnostics.update({
            "status": "success",
            "shortcode": shortcode,
            "selected_media": selection.get("selection", "video_versions"),
            "filename": destination.name,
            "page_route": route,
        })
        return str(destination), diagnostics

    diagnostics.update({
        "status": "failed",
        "shortcode": shortcode,
        "reason": last_reason,
    })
    return None, diagnostics
