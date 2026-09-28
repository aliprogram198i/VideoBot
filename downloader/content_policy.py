"""Content safety policy for download delivery.

This module only decides whether a requested source is adult content and, when
it is, supplies the configured safe replacement URL. It does not download or
resolve media itself.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

SAFE_REPLACEMENT_URL = "https://youtube.com/shorts/rn7tFtSO_eg?si=9ejKd177-eapRwX3"

# Existing project-specific adult source. Keep this list intentionally small
# and explicit to avoid false positives on ordinary sites.
ADULT_HOSTS = frozenset({"krx18.com"})

_ADULT_TERMS = re.compile(
    r"(?<![a-z0-9])(?:porn(?:ography)?|xxx|hentai|nsfw)(?![a-z0-9])",
    re.IGNORECASE,
)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _contains_adult_term(value: object) -> bool:
    return bool(_ADULT_TERMS.search(str(value or "")))


def is_adult_content_url(url: str, metadata: dict | None = None) -> bool:
    """Return True only for explicit adult-source/metadata signals."""
    host = _host(url)
    if host in ADULT_HOSTS or any(host.endswith("." + item) for item in ADULT_HOSTS):
        return True

    if not isinstance(metadata, dict):
        return False

    categories = metadata.get("categories")
    if isinstance(categories, (list, tuple, set)):
        if any(_contains_adult_term(item) for item in categories):
            return True

    for key in ("title", "description"):
        if _contains_adult_term(metadata.get(key)):
            return True

    return False


def safe_replacement_url() -> str:
    """Return the configured replacement content URL."""
    return SAFE_REPLACEMENT_URL


__all__ = ["SAFE_REPLACEMENT_URL", "is_adult_content_url", "safe_replacement_url"]
