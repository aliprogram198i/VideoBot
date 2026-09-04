"""Orchestration layer for deterministic smart media extraction.

This engine coordinates page/embed resolution, candidate validation, and
candidate ranking. It does not download media and does not implement
domain-specific rules or authentication/DRM bypasses.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .candidate_ranker import CandidateRanker
from .candidate_validator import CandidateValidator, ValidationResult
from .embed_resolver import EmbedResolver


@dataclass(frozen=True)
class ExtractionResult:
    """Complete result of one smart extraction attempt."""

    source_url: str
    best_media: ValidationResult | None
    ranked_candidates: tuple[ValidationResult, ...]
    visited_pages: tuple[str, ...]
    candidate_count: int
    valid_candidate_count: int
    invalid_candidate_count: int
    diagnostics: tuple[str, ...]


class SmartExtractionEngine:
    """Coordinate deterministic smart extraction components."""

    def __init__(
        self,
        resolver: EmbedResolver,
        validator: CandidateValidator,
        ranker: CandidateRanker,
    ) -> None:
        if not isinstance(resolver, EmbedResolver):
            raise TypeError("resolver must be an EmbedResolver")

        if not isinstance(validator, CandidateValidator):
            raise TypeError("validator must be a CandidateValidator")

        if not isinstance(ranker, CandidateRanker):
            raise TypeError("ranker must be a CandidateRanker")

        self.resolver = resolver
        self.validator = validator
        self.ranker = ranker

    def extract(
        self,
        source_url: str,
        *,
        timeout: float = 30.0,
        max_html_bytes: int = 5 * 1024 * 1024,
        validation_timeout: float = 15.0,
        max_ranked_candidates: int = 100,
    ) -> ExtractionResult:
        """Resolve, validate, and rank candidates from a source page."""

        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError("source_url must be a non-empty string")

        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        if max_html_bytes <= 0:
            raise ValueError("max_html_bytes must be greater than zero")

        if validation_timeout <= 0:
            raise ValueError("validation_timeout must be greater than zero")

        if max_ranked_candidates <= 0:
            raise ValueError(
                "max_ranked_candidates must be greater than zero"
            )

        diagnostics: list[str] = []

        try:
            resolution = self.resolver.resolve(
                source_url,
                timeout=timeout,
                max_html_bytes=max_html_bytes,
            )
        except Exception as exc:
            diagnostics.extend(
                [
                    f"resolution_failed:{type(exc).__name__}",
                    "no_pages_visited",
                    "no_candidates_validated",
                    "no_valid_media_candidate",
                ]
            )

            return ExtractionResult(
                source_url=source_url,
                best_media=None,
                ranked_candidates=(),
                visited_pages=(),
                candidate_count=0,
                valid_candidate_count=0,
                invalid_candidate_count=0,
                diagnostics=tuple(diagnostics),
            )

        if resolution.resolution_error is not None:
            diagnostics.append(
                f"resolution_failed:{resolution.resolution_error}"
            )

        validation_results: list[ValidationResult] = []

        candidates = list(resolution.candidates)
        max_concurrent_validations = min(4, len(candidates))

        def validate_one(candidate: Any) -> tuple[Any, ValidationResult | None, Exception | None]:
            try:
                result = self.validator.validate(
                    candidate,
                    timeout=validation_timeout,
                )
                return candidate, result, None
            except Exception as exc:
                return candidate, None, exc

        if max_concurrent_validations:
            with ThreadPoolExecutor(
                max_workers=max_concurrent_validations,
                thread_name_prefix="smart-validate",
            ) as executor:
                futures = [
                    executor.submit(validate_one, candidate)
                    for candidate in candidates
                ]

                # Consume futures in the original candidate order.
                # Network probes run concurrently, while result ordering
                # and diagnostics remain deterministic.
                for future in futures:
                    candidate, result, exc = future.result()

                    if exc is not None:
                        diagnostics.append(
                            f"validation_exception:{candidate.url}:"
                            f"{type(exc).__name__}"
                        )
                        continue

                    if result is None:
                        diagnostics.append(
                            f"validation_exception:{candidate.url}:RuntimeError"
                        )
                        continue

                    validation_results.append(result)

                    if not result.valid:
                        diagnostics.append(
                            f"candidate_rejected:{result.reason}:{candidate.url}"
                        )

        ranked = self.ranker.rank(
            validation_results,
            max_results=max_ranked_candidates,
        )

        media_ranked = [
            result
            for result in ranked
            if result.valid and result.candidate.kind != "iframe"
        ]

        best_media = media_ranked[0] if media_ranked else None

        valid_count = sum(
            1 for result in validation_results if result.valid
        )
        invalid_count = len(validation_results) - valid_count

        if not resolution.visited_pages:
            diagnostics.append("no_pages_visited")

        if not validation_results:
            diagnostics.append("no_candidates_validated")

        if not best_media:
            diagnostics.append("no_valid_media_candidate")

        return ExtractionResult(
            source_url=source_url,
            best_media=best_media,
            ranked_candidates=tuple(ranked),
            visited_pages=resolution.visited_pages,
            candidate_count=len(resolution.candidates),
            valid_candidate_count=valid_count,
            invalid_candidate_count=invalid_count,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def summarize(result: ExtractionResult) -> dict[str, Any]:
        """Return a compact diagnostic summary for logging/tests."""

        if not isinstance(result, ExtractionResult):
            raise TypeError("result must be an ExtractionResult")

        best = result.best_media

        return {
            "source_url": result.source_url,
            "best_url": best.candidate.url if best else None,
            "best_kind": best.candidate.kind if best else None,
            "visited_pages": len(result.visited_pages),
            "candidate_count": result.candidate_count,
            "valid_candidate_count": result.valid_candidate_count,
            "invalid_candidate_count": result.invalid_candidate_count,
            "diagnostics": list(result.diagnostics),
        }
