"""Conservative Facebook Reel URL variants for yt-dlp recovery.

Facebook's /reel/<id> extractor can fail even when the same media is exposed
through a legacy /<page>/videos/<id>/ URL. This module only derives URL forms
from the exact Reel id; it does not guess accounts, credentials, or unrelated
media.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_REEL_PATH = re.compile(r"^/reel/(\\d+)/?$", re.IGNORECASE)
_ALLOWED_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}


def facebook_reel_variants(source_url: str) -> list[str]:
    """Return conservative alternate Facebook URLs for a /reel/<id> source."""
    if not isinstance(source_url, str) or not source_url.strip():
        return []

    parsed = urlparse(source_url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _ALLOWED_HOSTS:
        return []

    match = _REEL_PATH.match(parsed.path)
    if not match:
        return []

    reel_id = match.group(1)
    variants = [
        f"https://www.facebook.com/facebook/videos/{reel_id}/",
    ]

    # Preserve deterministic order and avoid returning the original /reel URL.
    return list(dict.fromkeys(variants))
