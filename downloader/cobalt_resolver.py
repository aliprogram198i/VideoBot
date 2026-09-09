"""Cobalt API fallback for public media URLs.

This module only asks a self-hosted Cobalt instance to resolve/process a
public URL. It does not bypass authentication, CAPTCHA, DRM, or access
controls. The returned URL is intentionally compatible with the existing
media download pipeline, so large-file splitting/upload remains unchanged.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_TIMEOUT = float(os.getenv("ALIBOT_COBALT_TIMEOUT", "20"))


def _base_url() -> str:
    return os.getenv("ALIBOT_COBALT_URL", "").strip().rstrip("/")


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AliBot-Cobalt-Resolver/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        raw = response.read()
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def resolve(url: str) -> list[str]:
    """Return Cobalt-produced public/tunnel media URLs, or an empty list."""
    base = _base_url()
    if not base or not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return []

    payload = {
        "url": url,
        "videoQuality": "max",
        "audioFormat": "best",
        "downloadMode": "auto",
        "localProcessing": "disabled",
        "disableMetadata": True,
    }

    try:
        data = _post(f"{base}/", payload)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(f"⚠️ Cobalt Resolver request failed: {type(exc).__name__}", flush=True)
        return []
    except Exception as exc:
        print(f"⚠️ Cobalt Resolver unexpected failure: {type(exc).__name__}", flush=True)
        return []

    status = str(data.get("status", ""))
    candidate = data.get("url")
    if status in {"redirect", "tunnel"} and isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
        return [candidate]

    if status == "picker":
        # Picker items are intentionally not guessed: selecting an item needs
        # explicit bot/UI semantics and should not silently choose a stream.
        print("ℹ️ Cobalt Resolver returned picker; no automatic choice made", flush=True)
    elif status == "error":
        error = data.get("error")
        if isinstance(error, dict):
            print(f"ℹ️ Cobalt Resolver rejected URL: {error.get('code', 'unknown')}", flush=True)
        else:
            print("ℹ️ Cobalt Resolver rejected URL", flush=True)

    return []
