"""Orchestration layer for deterministic smart media extraction.

This engine coordinates page/embed resolution, candidate validation, and
candidate ranking. It does not download media and does not implement
domain-specific rules or authentication/DRM bypasses.

Validation is staged: the highest-confidence discovered candidates are probed
first. The remainder is used only when the first stage produces no usable
media. This bounds the common-case latency without permanently hiding a
fallback candidate.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable
import time

from .candidate_ranker import CandidateRanker
from .candidate_validator import CandidateValidator, ValidationResult
from .embed_resolver import EmbedResolver
from .smart_learning import SmartTelemetryStore, get_telemetry_store
from .instagram_identity import candidate_matches_instagram_source, parse_instagram_post_url
from .telegram_identity import candidate_matches_telegram_source, parse_telegram_post_url
from .resolver_budget import ResolverBudget
from .resolver_contracts import ResolverResult


DEFAULT_PRIMARY_VALIDATION_CANDIDATES = 24
DEFAULT_VALIDATION_WORKERS = 4


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
    telemetry_id: int | None = None


class SmartExtractionEngine:
    """Coordinate smart extraction components."""

    def __init__(
        self,
        resolver: EmbedResolver,
        validator: CandidateValidator,
        ranker: CandidateRanker,
        telemetry_store: SmartTelemetryStore | None = None,
    ) -> None:
        if not isinstance(resolver, EmbedResolver):
            raise TypeError("resolver must be an EmbedResolver")
        if not isinstance(validator, CandidateValidator):
            raise TypeError("validator must be a CandidateValidator")
        if not isinstance(ranker, CandidateRanker):
            raise TypeError("ranker must be a CandidateRanker")
        if telemetry_store is not None and not isinstance(telemetry_store, SmartTelemetryStore):
            raise TypeError("telemetry_store must be a SmartTelemetryStore or None")

        self.resolver = resolver
        self.validator = validator
        self.ranker = ranker
        self.telemetry_store = telemetry_store
        self.budget = ResolverBudget.from_environment()

    def _record_telemetry(self, result: ExtractionResult, started_at: float) -> ExtractionResult:
        store = self.telemetry_store
        if store is None:
            return result
        try:
            policy_version, _ = store.production_policy()
            telemetry_id = store.record_extraction(
                result,
                elapsed_ms=(time.perf_counter() - started_at) * 1000.0,
                policy_version=policy_version,
            )
            return ExtractionResult(
                source_url=result.source_url,
                best_media=result.best_media,
                ranked_candidates=result.ranked_candidates,
                visited_pages=result.visited_pages,
                candidate_count=result.candidate_count,
                valid_candidate_count=result.valid_candidate_count,
                invalid_candidate_count=result.invalid_candidate_count,
                diagnostics=result.diagnostics,
                telemetry_id=telemetry_id,
            )
        except Exception:
            return result

    @staticmethod
    def _apply_source_identity_gate(source_url: str, results: list[ValidationResult], diagnostics: list[str]) -> list[ValidationResult]:
        """Reject valid media candidates that cannot be tied to the exact source."""
        telegram_source = parse_telegram_post_url(source_url)
        instagram_source = parse_instagram_post_url(source_url)
        if telegram_source is None and instagram_source is None:
            return results
        accepted: list[ValidationResult] = []
        rejected = 0
        for result in results:
            if not result.valid or result.candidate.kind == "iframe":
                accepted.append(result)
                continue
            if telegram_source is not None:
                matches = candidate_matches_telegram_source(result.candidate, telegram_source)
                platform = "telegram"
            else:
                matches = candidate_matches_instagram_source(result.candidate, instagram_source)
                platform = "instagram"
            if not matches:
                rejected += 1
                diagnostics.append(f"source_identity_rejected:{platform}:{result.candidate.discovered_by}")
                continue
            accepted.append(result)
        if rejected:
            platform = "telegram" if telegram_source is not None else "instagram"
            diagnostics.append(f"source_identity_gate:{platform}:rejected={rejected}")
        return accepted

    def _validate_batch(
        self,
        candidates: Iterable[Any],
        *,
        validation_timeout: float,
        diagnostics: list[str],
    ) -> list[ValidationResult]:
        batch = list(candidates)
        if not batch:
            return []

        max_workers = min(DEFAULT_VALIDATION_WORKERS, len(batch))
        validation_results: list[ValidationResult] = []

        def validate_one(candidate: Any) -> tuple[Any, ValidationResult | None, Exception | None]:
            try:
                return candidate, self.validator.validate(candidate, timeout=validation_timeout), None
            except Exception as exc:
                return candidate, None, exc

        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="smart-validate",
        ) as executor:
            futures = [executor.submit(validate_one, candidate) for candidate in batch]
            for future in futures:
                candidate, result, exc = future.result()
                if exc is not None:
                    diagnostics.append(
                        f"validation_exception:{candidate.url}:{type(exc).__name__}"
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

        return validation_results

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
            raise ValueError("max_ranked_candidates must be greater than zero")

        started_at = time.perf_counter()
        diagnostics: list[str] = []
        budget = self.budget
        timeout, validation_timeout, max_html_bytes, max_ranked_candidates = budget.apply(
            timeout=timeout,
            validation_timeout=validation_timeout,
            max_html_bytes=max_html_bytes,
            max_ranked_candidates=max_ranked_candidates,
        )
        diagnostics.append(
            "resolver_budget:timeout=%ss:validation=%ss:html=%d:candidates=%d"
            % (timeout, validation_timeout, max_html_bytes, max_ranked_candidates)
        )

        try:
            resolution = self.resolver.resolve(
                source_url,
                timeout=timeout,
                max_html_bytes=max_html_bytes,
            )
        except Exception as exc:
            diagnostics.extend([
                f"resolution_failed:{type(exc).__name__}",
                "no_pages_visited",
                "no_candidates_validated",
                "no_valid_media_candidate",
            ])
            result = ExtractionResult(
                source_url=source_url,
                best_media=None,
                ranked_candidates=(),
                visited_pages=(),
                candidate_count=0,
                valid_candidate_count=0,
                invalid_candidate_count=0,
                diagnostics=tuple(diagnostics),
            )
            return self._record_telemetry(result, started_at)

        if resolution.resolution_error is not None:
            diagnostics.append(f"resolution_failed:{resolution.resolution_error}")

        resolver_contract = ResolverResult.from_output(
            "embed_resolver",
            resolution.candidates,
            elapsed_ms=(time.perf_counter() - started_at) * 1000.0,
            failure_reason=str(resolution.resolution_error) if resolution.resolution_error else None,
        )
        if resolver_contract.status == "empty":
            diagnostics.append("resolver_result:empty")

        candidates = list(resolution.candidates)
        primary = candidates[:DEFAULT_PRIMARY_VALIDATION_CANDIDATES]
        remainder = candidates[DEFAULT_PRIMARY_VALIDATION_CANDIDATES:]

        validation_results = self._validate_batch(
            primary,
            validation_timeout=validation_timeout,
            diagnostics=diagnostics,
        )
        validation_results = self._apply_source_identity_gate(source_url, validation_results, diagnostics)

        # Rank the first stage before deciding whether more network probes are
        # necessary. Obvious secondary/ad candidates are therefore not enough
        # to force probing of every discovered URL when a better media source
        # is already available.
        ranked = self.ranker.rank(
            validation_results,
            max_results=max_ranked_candidates,
        )
        media_ranked = [
            result for result in ranked
            if result.valid and result.candidate.kind != "iframe"
        ]

        if not media_ranked and remainder:
            diagnostics.append("primary_validation_exhausted")
            validation_results.extend(
                self._validate_batch(
                    remainder,
                    validation_timeout=validation_timeout,
                    diagnostics=diagnostics,
                )
            )
            validation_results = self._apply_source_identity_gate(source_url, validation_results, diagnostics)
            ranked = self.ranker.rank(
                validation_results,
                max_results=max_ranked_candidates,
            )
            media_ranked = [
                result for result in ranked
                if result.valid and result.candidate.kind != "iframe"
            ]

        best_media = media_ranked[0] if media_ranked else None
        valid_count = sum(1 for result in validation_results if result.valid)
        invalid_count = len(validation_results) - valid_count

        if not resolution.visited_pages:
            diagnostics.append("no_pages_visited")
        if not validation_results:
            diagnostics.append("no_candidates_validated")
        if not best_media:
            diagnostics.append("no_valid_media_candidate")

        result = ExtractionResult(
            source_url=source_url,
            best_media=best_media,
            ranked_candidates=tuple(ranked),
            visited_pages=resolution.visited_pages,
            candidate_count=len(resolution.candidates),
            valid_candidate_count=valid_count,
            invalid_candidate_count=invalid_count,
            diagnostics=tuple(diagnostics),
        )
        return self._record_telemetry(result, started_at)

    @staticmethod
    def summarize(result: ExtractionResult) -> dict[str, Any]:
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
            "telemetry_id": result.telemetry_id,
        }
