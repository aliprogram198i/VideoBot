"""Central resolver budgets and timeout policy for AliBot."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


@dataclass(frozen=True)
class ResolverBudget:
    extraction_timeout: float = 45.0
    validation_timeout: float = 12.0
    page_timeout: float = 20.0
    max_html_bytes: int = 5 * 1024 * 1024
    max_candidates: int = 100
    max_pages: int = 10

    @classmethod
    def from_environment(cls) -> "ResolverBudget":
        return cls(
            extraction_timeout=_positive_float("ALIBOT_RESOLVER_TIMEOUT_SECONDS", 45.0, 5.0, 90.0),
            validation_timeout=_positive_float("ALIBOT_VALIDATION_TIMEOUT_SECONDS", 12.0, 3.0, 30.0),
            page_timeout=_positive_float("ALIBOT_PAGE_TIMEOUT_SECONDS", 20.0, 3.0, 45.0),
            max_html_bytes=_positive_int("ALIBOT_RESOLVER_MAX_HTML_BYTES", 5 * 1024 * 1024, 256 * 1024, 20 * 1024 * 1024),
            max_candidates=_positive_int("ALIBOT_RESOLVER_MAX_CANDIDATES", 100, 10, 200),
            max_pages=_positive_int("ALIBOT_RESOLVER_MAX_PAGES", 10, 1, 30),
        )

    def apply(self, *, timeout: float, validation_timeout: float, max_html_bytes: int, max_ranked_candidates: int) -> tuple[float, float, int, int]:
        return (
            min(float(timeout), self.extraction_timeout),
            min(float(validation_timeout), self.validation_timeout),
            min(int(max_html_bytes), self.max_html_bytes),
            min(int(max_ranked_candidates), self.max_candidates),
        )
