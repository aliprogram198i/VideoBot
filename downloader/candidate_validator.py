"""Deterministic validation primitives for smart media candidates.

The validator does not implement its own SSRF policy. URL safety and network
access are injected by the caller so the existing bot security layer remains
the single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .smart_extractor import MediaCandidate


@dataclass(frozen=True)
class ValidationResult:
    """Validation outcome for one media candidate."""

    candidate: MediaCandidate
    valid: bool
    reason: str = ""
    status: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    metadata: dict[str, Any] | None = None


class CandidateValidator:
    """Validate candidates through injected URL and HTTP probe functions."""

    def __init__(
        self,
        url_validator: Callable[[str], Any],
        probe_function: Callable[..., Any],
    ) -> None:
        if not callable(url_validator):
            raise TypeError("url_validator must be callable")

        if not callable(probe_function):
            raise TypeError("probe_function must be callable")

        self._url_validator = url_validator
        self._probe_function = probe_function

    def validate(
        self,
        candidate: MediaCandidate,
        *,
        timeout: float = 15.0,
    ) -> ValidationResult:
        if not isinstance(candidate, MediaCandidate):
            raise TypeError("candidate must be a MediaCandidate")

        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        try:
            self._url_validator(candidate.url)
        except Exception as exc:
            return ValidationResult(
                candidate=candidate,
                valid=False,
                reason=f"url_validation_failed:{type(exc).__name__}",
            )

        try:
            probe = self._probe_function(
                candidate.url,
                timeout=timeout,
                kind=candidate.kind,
            )
        except Exception as exc:
            return ValidationResult(
                candidate=candidate,
                valid=False,
                reason=f"probe_failed:{type(exc).__name__}",
            )

        if not isinstance(probe, dict):
            raise TypeError("probe_function must return a dict")

        status = probe.get("status")
        content_type = probe.get("content_type")
        content_length = probe.get("content_length")

        if status is not None:
            try:
                status = int(status)
            except (TypeError, ValueError):
                return ValidationResult(
                    candidate=candidate,
                    valid=False,
                    reason="invalid_status",
                )

            if status < 200 or status >= 400:
                return ValidationResult(
                    candidate=candidate,
                    valid=False,
                    reason=f"http_status:{status}",
                    status=status,
                    content_type=content_type,
                    content_length=content_length,
                    metadata=probe,
                )

        if content_length is not None:
            try:
                content_length = int(content_length)
            except (TypeError, ValueError):
                return ValidationResult(
                    candidate=candidate,
                    valid=False,
                    reason="invalid_content_length",
                    status=status,
                    content_type=content_type,
                    metadata=probe,
                )

            if content_length < 0:
                return ValidationResult(
                    candidate=candidate,
                    valid=False,
                    reason="negative_content_length",
                    status=status,
                    content_type=content_type,
                    content_length=content_length,
                    metadata=probe,
                )

        if candidate.kind == "iframe":
            return ValidationResult(
                candidate=candidate,
                valid=True,
                reason="embed_page",
                status=status,
                content_type=content_type,
                content_length=content_length,
                metadata=probe,
            )

        normalized_type = (content_type or "").lower()

        clean_url = candidate.url.lower().split("?", 1)[0]

        if normalized_type:
            media_type_matches = {
                "hls": (
                    "mpegurl" in normalized_type
                    or "vnd.apple.mpegurl" in normalized_type
                ),
                "dash": "dash+xml" in normalized_type,
                "progressive": (
                    normalized_type.startswith("video/")
                    or normalized_type.startswith("audio/")
                ),
            }
        else:
            media_type_matches = {
                "hls": clean_url.endswith(".m3u8"),
                "dash": clean_url.endswith(".mpd"),
                "progressive": clean_url.endswith(
                    (".mp4", ".webm", ".mov")
                ),
            }

        if candidate.kind in media_type_matches and not media_type_matches[candidate.kind]:
            return ValidationResult(
                candidate=candidate,
                valid=False,
                reason="content_type_mismatch",
                status=status,
                content_type=content_type,
                content_length=content_length,
                metadata=probe,
            )

        return ValidationResult(
            candidate=candidate,
            valid=True,
            reason="validated",
            status=status,
            content_type=content_type,
            content_length=content_length,
            metadata=probe,
        )
