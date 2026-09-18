"""Dedicated Instagram recovery through the private AliBot Cobalt service.

This resolver is intentionally narrow: it only accepts canonical public
Instagram post/reel URLs and only runs for Instagram recovery. It does not
use cookies or authentication and does not fall back to arbitrary third-party
URLs. The Cobalt request is made against the private Railway service.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


DEFAULT_COBALT_URL = "http://cobalt-resolver:9000"
DEFAULT_TIMEOUT = 90
MAX_FILENAME = 120


def _cobalt_url() -> str:
    return os.getenv("ALIBOT_COBALT_URL", DEFAULT_COBALT_URL).rstrip("/")


def _safe_filename(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    raw = raw.strip("._-")[:MAX_FILENAME]
    return raw or fallback


def _canonicalize_instagram_url(url: str) -> str:
    """Normalize query encoding without changing the Instagram post identity."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    query = urlencode(parse_qsl(parsed.query, keep_blank_values=True), doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))


def _is_instagram_post_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"instagram.com", "www.instagram.com"}:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return (
        len(parts) == 2
        and parts[0].lower() in {"reel", "p", "tv"}
        and 1 <= len(parts[1]) <= 128
        and all(ch.isalnum() or ch in {"-", "_"} for ch in parts[1])
    )


def _read_json(response: Any, max_bytes: int = 512 * 1024) -> dict[str, Any]:
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("Cobalt response exceeded configured size")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("Cobalt returned a non-object response")
    return data


def _read_http_error(error: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        raw = error.read(512 * 1024)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _download_file(
    media_url: str,
    destination: Path,
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    timeout: int,
    max_bytes: int,
) -> Path:
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
            raise ValueError(f"Unexpected Cobalt media content type: {content_type}")

        with destination.open("wb") as output:
            total = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Cobalt media exceeded configured size")
                output.write(chunk)
        if total <= 0:
            raise ValueError("Cobalt returned an empty media file")
    finally:
        response.close()
    return destination


def download_instagram_with_cobalt(
    source_url: str,
    temp_dir: str | os.PathLike[str],
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = 49 * 1024 * 1024,
) -> tuple[str | None, dict[str, Any]]:
    """Download one exact Instagram post/reel through the private Cobalt service."""
    diagnostics: dict[str, Any] = {
        "resolver": "cobalt_instagram",
        "status": "not_attempted",
        "source_url": source_url,
    }

    if not _is_instagram_post_url(source_url):
        diagnostics.update({"status": "skipped", "reason": "not_instagram_post_url"})
        return None, diagnostics

    payload = {
        "url": source_url,
        "downloadMode": "auto",
        "videoQuality": "max",
        "disableMetadata": True,
        "alwaysProxy": True,
        "localProcessing": "disabled",
    }
    body = json.dumps(payload).encode("utf-8")
    request = request_factory(
        _cobalt_url() + "/",
        data=body,
        headers={
            "User-Agent": "AliBot/InstagramResolver",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        try:
            response = open_function(request, timeout=timeout, max_bytes=512 * 1024)
        except urllib.error.HTTPError as exc:
            error_data = _read_http_error(exc)
            diagnostics.update({
                "status": "http_error",
                "http_status": exc.code,
            })
            error = error_data.get("error")
            if isinstance(error, dict):
                diagnostics["error_code"] = error.get("code")
                diagnostics["error_context"] = error.get("context")
            if not diagnostics.get("error_code"):
                diagnostics["reason"] = f"cobalt_http_{exc.code}"
            print(
                "⚠️ Instagram Cobalt Resolver: "
                f"HTTP {exc.code} code={diagnostics.get('error_code', 'unknown')}"
            )
            return None, diagnostics

        try:
            data = _read_json(response)
        finally:
            response.close()

        status = data.get("status")
        diagnostics["cobalt_status"] = status

        if status in {"tunnel", "redirect"}:
            media_url = data.get("url")
        elif status == "picker":
            items = data.get("picker")
            videos = [
                item.get("url")
                for item in items
                if isinstance(item, dict)
                and item.get("type") == "video"
                and isinstance(item.get("url"), str)
            ] if isinstance(items, list) else []
            media_url = videos[0] if len(videos) == 1 else None
            if len(videos) != 1:
                diagnostics["reason"] = "ambiguous_instagram_picker"
        else:
            media_url = None
            error = data.get("error")
            if isinstance(error, dict):
                diagnostics["error_code"] = error.get("code")
                diagnostics["error_context"] = error.get("context")

        if not isinstance(media_url, str) or not media_url.startswith(("http://", "https://")):
            diagnostics["status"] = "failed"
            diagnostics.setdefault("reason", "no_media_url")
            print(
                "⚠️ Instagram Cobalt Resolver: "
                f"status={status} code={diagnostics.get('error_code', 'none')} "
                f"reason={diagnostics.get('reason', 'none')}"
            )
            return None, diagnostics

        shortcode = urlparse(source_url).path.rstrip("/").split("/")[-1]
        fallback = f"instagram_{shortcode}.mp4"
        destination = Path(temp_dir) / _safe_filename(data.get("filename"), fallback)
        if destination.suffix.lower() not in {".mp4", ".webm", ".mov", ".mkv"}:
            destination = destination.with_suffix(".mp4")

        _download_file(
            media_url,
            destination,
            request_factory=request_factory,
            open_function=open_function,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        diagnostics.update({
            "status": "success",
            "selected_media": "cobalt",
            "filename": destination.name,
        })
        return str(destination), diagnostics

    except Exception as exc:
        diagnostics.update({
            "status": "exception",
            "exception_type": type(exc).__name__,
            "error_message": str(exc)[:1000],
        })
        print(
            "⚠️ Instagram Cobalt Resolver: "
            f"exception={type(exc).__name__}"
        )
        return None, diagnostics
