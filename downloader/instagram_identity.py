"""Instagram source identity primitives.

These helpers establish a stable identity for public Instagram media posts
and prevent generic media resolvers from accepting a candidate discovered
from a different post/page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


_INSTAGRAM_HOSTS = frozenset({
    "instagram.com",
    "www.instagram.com",
})


@dataclass(frozen=True)
class InstagramPostIdentity:
    """Canonical identity of one public Instagram post/reel."""

    shortcode: str

    @property
    def key(self) -> str:
        return self.shortcode


def parse_instagram_post_url(url: str) -> InstagramPostIdentity | None:
    """Parse supported public Instagram post URL forms.

    Supported route families:
      https://www.instagram.com/reel/SHORTCODE/
      https://www.instagram.com/p/SHORTCODE/
      https://www.instagram.com/tv/SHORTCODE/

    Query strings/fragments are ignored. Shortcodes are case-sensitive and
    therefore intentionally preserved exactly.
    """
    if not isinstance(url, str) or not url.strip():
        return None

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None

    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _INSTAGRAM_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None

    route, shortcode = parts
    if route.lower() not in {"reel", "p", "tv"}:
        return None

    if not shortcode or len(shortcode) > 128:
        return None

    # Instagram shortcodes use URL-safe characters. Keep this conservative
    # so arbitrary paths cannot become an identity.
    if not all(char.isalnum() or char in {"-", "_"} for char in shortcode):
        return None

    return InstagramPostIdentity(shortcode=shortcode)


def instagram_post_identity(url: str) -> InstagramPostIdentity | None:
    """Compatibility alias for callers that prefer a noun-style API."""
    return parse_instagram_post_url(url)


def is_instagram_public_post_url(url: str) -> bool:
    """Return True only for a parseable public Instagram post URL."""
    return parse_instagram_post_url(url) is not None


def candidate_matches_instagram_source(
    candidate: Any,
    source_identity: InstagramPostIdentity,
) -> bool:
    """Accept only candidates with explicit exact-post Instagram provenance.

    A matching source_page alone is not sufficient: generic HTML/embed/browser
    discovery can attach a neighboring media URL to the requested page URL.
    The candidate must carry explicit shortcode provenance from the resolver
    that discovered it.
    """
    if source_identity is None:
        return False

    source_page = getattr(candidate, "source_page", None)
    candidate_identity = parse_instagram_post_url(source_page)
    if candidate_identity is None or candidate_identity.key != source_identity.key:
        return False

    metadata = getattr(candidate, "metadata", None)
    if not isinstance(metadata, dict):
        return False

    provenance = str(
        metadata.get("instagram_shortcode", "")
    ).strip()

    return provenance == source_identity.key
