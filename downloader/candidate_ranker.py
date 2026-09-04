"""Deterministic candidate ranking for the smart extraction engine."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .candidate_validator import ValidationResult
from .smart_extractor import MediaCandidate


_KIND_BONUS = {
    "hls": 30,
    "dash": 25,
    "progressive": 20,
    "iframe": 0,
}

_DISCOVERY_BONUS = {
    "video": 12,
    "source": 10,
    "script": 6,
    "attribute": 3,
    "iframe": 0,
}


class CandidateRanker:
    """Rank validated media candidates deterministically."""

    def score(self, result: ValidationResult) -> int:
        if not isinstance(result, ValidationResult):
            raise TypeError("result must be a ValidationResult")

        if not result.valid:
            return -10_000

        candidate = result.candidate

        score = candidate.score
        score += _KIND_BONUS.get(candidate.kind, 0)
        score += _DISCOVERY_BONUS.get(candidate.discovered_by, 0)

        # Recursive discovery is useful, but deeper branches are less
        # preferable when equivalent candidates exist.
        score -= candidate.depth * 8

        if result.status == 200:
            score += 5

        content_type = (result.content_type or "").lower()

        if candidate.kind == "hls" and "mpegurl" in content_type:
            score += 8

        if candidate.kind == "dash" and "dash+xml" in content_type:
            score += 8

        if candidate.kind == "progressive" and content_type.startswith("video/"):
            score += 8

        return score

    def rank(
        self,
        results: Iterable[ValidationResult],
        *,
        max_results: int = 100,
    ) -> list[ValidationResult]:
        if max_results <= 0:
            raise ValueError("max_results must be greater than zero")

        unique: dict[tuple[str, str], ValidationResult] = {}

        for result in results:
            if not isinstance(result, ValidationResult):
                raise TypeError("results must contain ValidationResult objects")

            key = (result.candidate.url, result.candidate.kind)

            existing = unique.get(key)

            if existing is None or self.score(result) > self.score(existing):
                unique[key] = result

        ranked = sorted(
            unique.values(),
            key=lambda result: (
                -self.score(result),
                result.candidate.depth,
                result.candidate.url,
            ),
        )

        return ranked[:max_results]

    def best(
        self,
        results: Iterable[ValidationResult],
    ) -> ValidationResult | None:
        ranked = self.rank(results, max_results=1)
        return ranked[0] if ranked else None
