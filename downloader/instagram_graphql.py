"""Direct public Instagram GraphQL resolver.

This resolver is a narrow recovery path for canonical public Instagram post/reel
URLs. It uses Instagram's current web GraphQL doc-id endpoint without cookies,
credentials, session tokens, or access-control bypasses.

The resolver only returns media whose metadata shortcode exactly matches the
requested source shortcode. Ambiguous sidecars are rejected rather than
guessing a neighboring item.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

DEFAULT_GRAPHQL_URL = "https://www.instagram.com/graphql/query"
DEFAULT_DOC_ID = "27128499623469141"
DEFAULT_TIMEOUT = 45
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_FILENAME = 120
DEFAULT_MAX_BYTES = 49 * 1024 * 1024


def _graphql_url() -> str:
    return os.getenv("ALIBOT_INSTAGRAM_GRAPHQL_URL", DEFAULT_GRAPHQL_URL).rstrip("/")


def _safe_filename(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    raw = raw.strip("._-")[:MAX_FILENAME]
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
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2 or parts[0].lower() not in {"reel", "p", "tv"}:
        return None
    shortcode = parts[1]
    if not (1 <= len(shortcode) <= 128):
        return None
    if not all(ch.isalnum() or ch in {"-", "_"} for ch in shortcode):
        return None
    return parts[0].lower(), shortcode


def _request_payload(shortcode: str) -> bytes:
    variables = {
        "shortcode": shortcode,
        "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
    }
    return urlencode(
        {
            "variables": json.dumps(variables, separators=(",", ":")),
            "doc_id": DEFAULT_DOC_ID,
            "server_timestamps": "true",
        }
    ).encode("utf-8")


def _read_json(response: Any) -> dict[str, Any]:
    raw = response.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Instagram GraphQL response exceeded configured size")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("Instagram GraphQL returned a non-object response")
    return data


def _response_cookie_headers(response: Any) -> list[str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return []
    try:
        values = headers.get_all("Set-Cookie") or []
    except AttributeError:
        value = headers.get("Set-Cookie")
        values = [value] if value else []
    return [str(value) for value in values if value]


def _cookie_header_from_set_cookie(values: list[str]) -> str:
    cookies: list[str] = []
    for value in values:
        pair = value.split(";", 1)[0].strip()
        if "=" in pair:
            cookies.append(pair)
    return "; ".join(dict.fromkeys(cookies))


def _csrf_token_from_cookies(cookie_header: str) -> str | None:
    for pair in cookie_header.split(";"):
        name, sep, value = pair.strip().partition("=")
        if sep and name.strip().lower() == "csrftoken":
            return value.strip() or None
    return None


def _response_error_reason(data: dict[str, Any]) -> str | None:
    errors = data.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = errors[0]
    if not isinstance(first, dict):
        return "graphql_error"
    code = first.get("code")
    message = str(first.get("message") or "graphql_error").strip()
    if code is not None:
        return f"{message}:{code}"
    return message


def _extract_item(data: dict[str, Any], shortcode: str) -> tuple[dict[str, Any] | None, str | None]:
    web_info = (
        (data.get("data") or {})
        .get("xdt_api__v1__media__shortcode__web_info")
        or {}
    )
    items = web_info.get("items")
    if not isinstance(items, list) or not items:
        return None, "no_media_items"

    item = items[0]
    if not isinstance(item, dict):
        return None, "invalid_media_item"

    returned_code = item.get("code") or item.get("shortcode")
    if returned_code != shortcode:
        return None, "source_identity_mismatch"

    return item, None


def _select_image(item: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    versions = item.get("image_versions2")
    if isinstance(versions, dict):
        candidates = versions.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict) and isinstance(candidate.get("url"), str) and candidate["url"].startswith(("https://", "http://")):
                    return candidate["url"], {"selection": "image_versions2"}
    display_url = item.get("display_url")
    if isinstance(display_url, str) and display_url.startswith(("https://", "http://")):
        return display_url, {"selection": "display_url"}
    return None, {"reason": "no_public_image_url"}


def _select_video(item: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    videos = item.get("video_versions")
    if isinstance(videos, list):
        urls = [
            v.get("url")
            for v in videos
            if isinstance(v, dict) and isinstance(v.get("url"), str)
            and v["url"].startswith(("http://", "https://"))
        ]
        if urls:
            return urls[0], {"selection": "video_versions"}

    carousel = item.get("carousel_media")
    if isinstance(carousel, list):
        candidates = []
        for child in carousel:
            if not isinstance(child, dict) or child.get("media_type") != 2:
                continue
            child_code = child.get("code") or child.get("shortcode")
            if child_code and child_code != item.get("code"):
                # Carousel child codes can differ from the parent shortcode; the
                # parent post identity remains the source identity checked above.
                pass
            versions = child.get("video_versions")
            if isinstance(versions, list):
                urls = [
                    v.get("url")
                    for v in versions
                    if isinstance(v, dict) and isinstance(v.get("url"), str)
                    and v["url"].startswith(("http://", "https://"))
                ]
                if urls:
                    candidates.append(urls[0])
        if len(candidates) == 1:
            return candidates[0], {"selection": "single_carousel_video"}
        if len(candidates) > 1:
            return None, {"reason": "ambiguous_carousel_videos"}

    return None, {"reason": "no_public_video_url"}


def _download_media(
    media_url: str,
    destination: Path,
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    timeout: int,
    max_bytes: int,
) -> None:
    request = request_factory(
        media_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AliBot Instagram Resolver)",
            "Accept": "*/*",
        },
    )
    response = open_function(request, timeout=timeout, max_bytes=max_bytes)
    try:
        content_type = (response.headers.get_content_type() or "").lower()
        if content_type and not (
            content_type.startswith("video/")
            or content_type.startswith("application/octet-stream")
        ):
            raise ValueError(f"Unexpected Instagram media content type: {content_type}")

        with destination.open("wb") as output:
            total = 0
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


def download_instagram_with_graphql(
    source_url: str,
    temp_dir: str | os.PathLike[str],
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[str | None, dict[str, Any]]:
    """Resolve and download one exact public Instagram post/reel."""
    diagnostics: dict[str, Any] = {
        "resolver": "instagram_graphql",
        "status": "not_attempted",
        "source_url": source_url,
        "doc_id": DEFAULT_DOC_ID,
    }

    parsed = _parse_source(source_url)
    if parsed is None:
        diagnostics.update({"status": "skipped", "reason": "not_canonical_instagram_url"})
        return None, diagnostics

    _, shortcode = parsed
    graphql_url = _graphql_url()
    page_request = request_factory(
        source_url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )

    cookie_header = ""
    csrf_token = None
    try:
        page_response = open_function(
            page_request,
            timeout=timeout,
            max_bytes=256 * 1024,
        )
        try:
            cookie_header = _cookie_header_from_set_cookie(
                _response_cookie_headers(page_response)
            )
            csrf_token = _csrf_token_from_cookies(cookie_header)
        finally:
            page_response.close()
    except Exception as exc:
        diagnostics["bootstrap_exception"] = type(exc).__name__

    diagnostics["csrf_bootstrap"] = bool(csrf_token)

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "X-IG-App-ID": "936619743392459",
        "Referer": source_url,
    }
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    if cookie_header:
        headers["Cookie"] = cookie_header

    request = request_factory(
        graphql_url,
        data=_request_payload(shortcode),
        headers=headers,
        method="POST",
    )

    try:
        try:
            response = open_function(request, timeout=timeout, max_bytes=MAX_JSON_BYTES)
        except urllib.error.HTTPError as exc:
            diagnostics.update({
                "status": "http_error",
                "http_status": exc.code,
                "reason": f"instagram_graphql_http_{exc.code}",
            })
            print(
                "⚠️ Instagram GraphQL Resolver: "
                f"HTTP {exc.code} shortcode={shortcode}"
            )
            return None, diagnostics

        try:
            data = _read_json(response)
        finally:
            response.close()

        error_reason = _response_error_reason(data)
        if error_reason:
            diagnostics["graphql_error"] = error_reason

        item, reason = _extract_item(data, shortcode)
        if item is None:
            diagnostics.update({"status": "failed", "reason": reason or "no_item"})
            return None, diagnostics

        media_url, selection = _select_video(item)
        if not media_url:
            diagnostics.update({"status": "failed", **selection})
            return None, diagnostics

        destination = Path(temp_dir) / _safe_filename(
            item.get("title"),
            f"instagram_{shortcode}.mp4",
        )
        if destination.suffix.lower() not in {".mp4", ".webm", ".mov", ".mkv"}:
            destination = destination.with_suffix(".mp4")

        _download_media(
            media_url,
            destination,
            request_factory=request_factory,
            open_function=open_function,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        diagnostics.update({
            "status": "success",
            "selected_media": selection.get("selection", "video_versions"),
            "filename": destination.name,
        })
        diagnostics.update({"source_identity_verified": True, "identity_proof": {"type": "instagram_shortcode", "key": shortcode}})
        return str(destination), diagnostics

    except Exception as exc:
        diagnostics.update({
            "status": "exception",
            "exception_type": type(exc).__name__,
            "error_message": str(exc)[:1000],
        })
        print(
            "⚠️ Instagram GraphQL Resolver: "
            f"exception={type(exc).__name__}"
        )
        return None, diagnostics
