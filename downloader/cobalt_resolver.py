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
    configured = os.getenv("ALIBOT_COBALT_URL", "").strip().rstrip("/")
    if configured:
        return configured

    # Staging has no spare Railway resource for a duplicate Cobalt service.
    # Keep this fallback staging-only so Production never acquires an implicit
    # cross-environment dependency.
    if os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() == "staging":
        return "https://cobalt-resolver-production.up.railway.app"

    return ""


def _facebook_variants(url: str) -> list[str]:
    """Return deterministic Facebook Reel URL variants for Cobalt.

    Facebook's /reel/ route can be rejected while the equivalent watch?v=
    route is accepted by downstream extractors. The numeric ID is preserved;
    no search or unrelated URL is introduced.
    """
    try:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
            return [url]

        parts = [part for part in parsed.path.split("/") if part]
        reel_id = parts[1] if len(parts) == 2 and parts[0].lower() == "reel" else ""
        if not reel_id and parsed.path.rstrip("/").lower() == "/watch":
            reel_id = (parse_qs(parsed.query).get("v") or [""])[0]

        if not reel_id.isdigit():
            return [url]

        return list(dict.fromkeys([
            url,
            f"https://www.facebook.com/watch/?v={reel_id}",
            f"https://m.facebook.com/watch/?v={reel_id}&_rdr",
        ]))
    except Exception:
        return [url]


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

    for variant in _facebook_variants(url):
        variant_payload = dict(payload)
        variant_payload["url"] = variant

        try:
            data = _post(f"{base}/", variant_payload)
        except urllib.error.HTTPError as exc:
            # Cobalt returns structured JSON error codes on HTTP 400. Keep the
            # exact upstream code visible so failures are diagnosable.
            try:
                raw_error = exc.read(4096).decode("utf-8", "replace")
            except Exception:
                raw_error = ""
            print(
                f"⚠️ Cobalt Resolver HTTP {exc.code} for {variant}: "
                f"{raw_error[:500]}",
                flush=True,
            )
            continue
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            print(
                f"⚠️ Cobalt Resolver request failed for {variant}: {type(exc).__name__}",
                flush=True,
            )
            continue
        except Exception as exc:
            print(
                f"⚠️ Cobalt Resolver unexpected failure for {variant}: {type(exc).__name__}",
                flush=True,
            )
            continue

        status = str(data.get("status", ""))
        candidate = data.get("url")

        if status in {"redirect", "tunnel"} and isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            print(f"✅ Cobalt Resolver: resolved via {variant}", flush=True)
            return [candidate]

        if status == "picker":
            # Picker items are intentionally not guessed: selecting an item needs
            # explicit bot/UI semantics and should not silently choose a stream.
            print(
                f"ℹ️ Cobalt Resolver returned picker for {variant}; no automatic choice made",
                flush=True,
            )
        elif status == "error":
            error = data.get("error")
            if isinstance(error, dict):
                print(
                    f"ℹ️ Cobalt Resolver rejected {variant}: "
                    f"{error.get('code', 'unknown')}",
                    flush=True,
                )
            else:
                print(f"ℹ️ Cobalt Resolver rejected {variant}", flush=True)

    return []
