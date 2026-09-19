"""Stable resolver result and candidate-identity contracts.

Resolvers may remain internally different. The public contract keeps the
orchestrator independent from provider-specific implementation details and
makes source-identity failures explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class ResolverResult:
    resolver: str
    source_url: str
    candidates: tuple[Any, ...] = ()
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    elapsed_ms: int = 0
    terminal: bool = False

    def __post_init__(self) -> None:
        if not self.resolver:
            raise ValueError("resolver must be non-empty")
        if not self.source_url:
            raise ValueError("source_url must be non-empty")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if int(self.elapsed_ms) < 0:
            raise ValueError("elapsed_ms must be non-negative")


@dataclass(frozen=True)
class IdentityGateResult:
    required: bool
    matched_candidates: int
    source_key: str | None
    accepted_source_page: str | None
    reason: str


def _valid_non_iframe(item: Any) -> bool:
    candidate = getattr(item, "candidate", None)
    return bool(
        getattr(item, "valid", False)
        and candidate is not None
        and getattr(candidate, "kind", None) != "iframe"
    )


def enforce_source_identity(
    source_url: str,
    ranked_candidates: Iterable[Any],
    best_candidate: Any,
    *,
    telegram_parser: Callable[[str], Any],
    telegram_matcher: Callable[[Any, Any], bool],
    instagram_parser: Callable[[str], Any],
    instagram_matcher: Callable[[Any, Any], bool],
) -> tuple[Any | None, IdentityGateResult]:
    """Select a candidate only when a protected social source is proven.

    For ordinary URLs the best candidate is preserved. Telegram/Instagram are
    fail-closed: a generic valid media URL is never accepted without identity.
    """
    telegram_source = telegram_parser(source_url)
    instagram_source = instagram_parser(source_url)
    ranked = list(ranked_candidates)

    if telegram_source is not None:
        matches = [
            item for item in ranked
            if _valid_non_iframe(item) and telegram_matcher(item.candidate, telegram_source)
        ]
        if not matches:
            return None, IdentityGateResult(
                required=True,
                matched_candidates=0,
                source_key=getattr(telegram_source, "key", None),
                accepted_source_page=None,
                reason="telegram_source_identity_unverified",
            )
        chosen = matches[0].candidate
        return chosen, IdentityGateResult(
            required=True,
            matched_candidates=len(matches),
            source_key=getattr(telegram_source, "key", None),
            accepted_source_page=getattr(chosen, "source_page", None),
            reason="verified",
        )

    if instagram_source is not None:
        matches = [
            item for item in ranked
            if _valid_non_iframe(item) and instagram_matcher(item.candidate, instagram_source)
        ]
        if not matches:
            return None, IdentityGateResult(
                required=True,
                matched_candidates=0,
                source_key=getattr(instagram_source, "key", None),
                accepted_source_page=None,
                reason="instagram_source_identity_unverified",
            )
        chosen = matches[0].candidate
        return chosen, IdentityGateResult(
            required=True,
            matched_candidates=len(matches),
            source_key=getattr(instagram_source, "key", None),
            accepted_source_page=getattr(chosen, "source_page", None),
            reason="verified",
        )

    return best_candidate, IdentityGateResult(
        required=False,
        matched_candidates=1 if best_candidate is not None else 0,
        source_key=None,
        accepted_source_page=getattr(best_candidate, "source_page", None),
        reason="unprotected_source",
    )
