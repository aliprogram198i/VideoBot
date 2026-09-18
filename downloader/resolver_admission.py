"""Single admission contract for resolver-produced local media.

All resolver paths that return a local file must pass through this module before
the file is allowed to continue toward Telegram delivery.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from downloader.instagram_identity import parse_instagram_post_url


def _inside(path: str, root: str | None) -> bool:
    if not root:
        return True
    try:
        candidate = Path(path).resolve()
        base = Path(root).resolve()
        return candidate == base or base in candidate.parents
    except OSError:
        return False


def admit_local_media(
    source_url: str,
    media_file: str | None,
    *,
    temp_dir: str | None = None,
    resolver: str | None = None,
    diagnostics: dict | None = None,
) -> tuple[str | None, dict]:
    """Fail-closed admission for direct resolver artifacts."""
    details = dict(diagnostics or {})
    result = {
        "admitted": False,
        "resolver": resolver or details.get("resolver") or "unknown",
        "reason": "not_admitted",
    }

    if not isinstance(source_url, str) or not source_url:
        result["reason"] = "missing_source_url"
        return None, result

    if not isinstance(media_file, str) or not media_file:
        result["reason"] = "missing_media_file"
        return None, result

    try:
        path = Path(media_file).resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            result["reason"] = "missing_or_empty_media"
            return None, result
    except OSError:
        result["reason"] = "media_stat_failed"
        return None, result

    if not _inside(str(path), temp_dir):
        result["reason"] = "media_outside_temp_dir"
        return None, result

    resolver_name = result["resolver"]
    parsed_instagram = parse_instagram_post_url(source_url)
    if parsed_instagram is not None:
        if resolver_name not in {
            "instagram_relay_html",
            "instagram_graphql",
            "cobalt_instagram",
        }:
            result["reason"] = "instagram_untrusted_resolver"
            return None, result
        if details.get("source_identity_verified") is not True:
            result["reason"] = "instagram_identity_proof_missing"
            return None, result

        result["source_identity"] = parsed_instagram.key
        result["identity_proof"] = details.get("identity_proof") or {
            "type": "instagram_shortcode",
            "key": parsed_instagram.key,
        }

    result.update({"admitted": True, "reason": "source_identity_and_file_verified"})
    return str(path), result
