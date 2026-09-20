"""Bounded FastSaverAPI resolver for public Facebook Reels.

The provider returns a short-lived Facebook CDN URL. This module verifies that
the provider echoed the exact source URL before returning the CDN candidate, so
the existing download pipeline never receives an unrelated Facebook asset.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .facebook_identity import parse_facebook_reel_url

API_URL = "https://api.fastsaver.io/v1/fetch"
DEFAULT_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 256 * 1024


def _api_key() -> str:
    return os.getenv("ALIBOT_FASTSAVER_KEY", "").strip()


def resolve(
    source_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[str]:
    """Resolve one exact public Facebook Reel to a signed CDN URL."""
    identity = parse_facebook_reel_url(source_url)
    key = _api_key()

    if identity is None or not key or timeout <= 0:
        return []

    source = identity.source_url
    query = urllib.parse.urlencode({"url": source})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        method="GET",
        headers={
            "Accept": "application/json",
            "X-Api-Key": key,
            "User-Agent": "AliBot-Facebook-Resolver/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(2048).decode("utf-8", "replace")
        except Exception:
            detail = ""
        print(
            f"⚠️ FastSaverAPI HTTP {exc.code}: {detail[:300]}",
            flush=True,
        )
        return []
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(
            f"⚠️ FastSaverAPI request failed: {type(exc).__name__}",
            flush=True,
        )
        return []

    if len(raw) > MAX_RESPONSE_BYTES:
        print("⚠️ FastSaverAPI response exceeded safety limit", flush=True)
        return []

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("⚠️ FastSaverAPI returned invalid JSON", flush=True)
        return []

    if not isinstance(data, dict) or data.get("ok") is not True:
        detail = data.get("detail") if isinstance(data, dict) else None
        print(
            f"ℹ️ FastSaverAPI did not resolve Reel: {str(detail or 'unknown')[:300]}",
            flush=True,
        )
        return []

    echoed_id = data.get("id")
    download_url = data.get("download_url")

    # The provider documents id as the exact URL supplied. Fail closed if it
    # does not echo our exact source URL, preventing cross-source candidates.
    if echoed_id != source:
        print("🛡️ FastSaverAPI identity gate: response source mismatch", flush=True)
        return []

    if not isinstance(download_url, str):
        return []

    parsed = urllib.parse.urlparse(download_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not (
        host == "fbcdn.net" or host.endswith(".fbcdn.net")
    ):
        print("🛡️ FastSaverAPI candidate gate: non-Facebook-CDN URL rejected", flush=True)
        return []

    print("✅ FastSaverAPI: exact Facebook Reel resolved", flush=True)
    return [download_url]


__all__ = ["resolve"]
