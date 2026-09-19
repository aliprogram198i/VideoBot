"""Canonical source identity and candidate provenance gate.

This module centralizes platform-specific source identity checks without
changing existing resolver or candidate contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    native: TelegramPostIdentity | InstagramPostIdentity

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
            return True
        return candidate_matches_source(candidate, self.source_identity)

    def filter_results(self, results: list[Any], diagnostics: list[str]) -> list[Any]:
        """Filter validated results and record deterministic rejection reasons."""
        if self.source_identity is None:
            return results

        accepted: list[Any] = []
        rejected = 0
        for result in results:
            if not getattr(result, "valid", False):
                accepted.append(result)
                continue
            candidate = getattr(result, "candidate", None)
            if self.accepts(candidate):
                accepted.append(result)
                continue
            rejected += 1
            diagnostics.append(
                "source_identity_rejected:%s:%s"
                % (
                    self.source_identity.platform,
                    getattr(candidate, "discovered_by", "unknown"),
                )
            )

        if rejected:
            diagnostics.append(
                "source_identity_gate:%s:rejected=%d"
                % (self.source_identity.platform, rejected)
            )
        return accepted


__all__ = [
    "CandidateIdentityGate",
    "SourceIdentity",
    "candidate_matches_source",
    "resolve_source_identity",
]
