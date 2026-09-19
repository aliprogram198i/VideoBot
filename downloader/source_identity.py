"""Canonical source identity and candidate provenance gate.

This module centralizes platform-specific source identity checks without
changing existing resolver or candidate contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .facebook_identity import (
    FacebookReelIdentity,
    candidate_matches_facebook_reel,
    parse_facebook_reel_url,
)
from .instagram_identity import (
    InstagramPostIdentity,
    candidate_matches_instagram_source,
    parse_instagram_post_url,
)
from .telegram_identity import (
    TelegramPostIdentity,
    candidate_matches_telegram_source,
    parse_telegram_post_url,
)


@dataclass(frozen=True)
class SourceIdentity:
    """Canonical identity for a source URL when exact identity is supported."""

    platform: str
    value: str
    native: TelegramPostIdentity | InstagramPostIdentity | FacebookReelIdentity

    @property
    def key(self) -> tuple[str, str]:
        return self.platform, self.value


def resolve_source_identity(source_url: str) -> SourceIdentity | None:
    """Return an exact identity for supported platforms, otherwise None."""
    telegram = parse_telegram_post_url(source_url)
    if telegram is not None:
        return SourceIdentity(
            platform="telegram",
            value=f"{telegram.channel}/{telegram.message_id}",
            native=telegram,
        )

    instagram = parse_instagram_post_url(source_url)
    if instagram is not None:
        return SourceIdentity(
            platform="instagram",
            value=instagram.shortcode,
            native=instagram,
        )

    facebook = parse_facebook_reel_url(source_url)
    if facebook is not None:
        return SourceIdentity(
            platform="facebook",
            value=facebook.reel_id,
            native=facebook,
        )

    return None


def candidate_matches_source(
    candidate: Any,
    source_identity: SourceIdentity,
) -> bool:
    """Return True only when candidate provenance matches the exact source."""
    if not isinstance(source_identity, SourceIdentity):
        return False

    if source_identity.platform == "telegram" and isinstance(
        source_identity.native, TelegramPostIdentity
    ):
        return candidate_matches_telegram_source(candidate, source_identity.native)

    if source_identity.platform == "instagram" and isinstance(
        source_identity.native, InstagramPostIdentity
    ):
        return candidate_matches_instagram_source(candidate, source_identity.native)

    if source_identity.platform == "facebook" and isinstance(
        source_identity.native, FacebookReelIdentity
    ):
        return candidate_matches_facebook_reel(candidate, source_identity.native)

    return False


class CandidateIdentityGate:
    """Deterministic gate applied after technical candidate validation."""

    def __init__(self, source_url: str) -> None:
        self.source_identity = resolve_source_identity(source_url)

    def accepts(self, candidate: Any) -> bool:
        """Keep unsupported-platform behavior backward compatible."""
        if self.source_identity is None:
            return True
        if getattr(candidate, "kind", None) == "iframe":
            return False
        return candidate_matches_source(candidate, self.source_identity)
