"""Statistical validation for paired resolver evidence.

This layer only measures whether a pairwise resolver advantage is supported by
paired observations. It does not select, reorder, explore, or invoke any
resolver. The conservative gates are deliberately stricter than a raw success
rate comparison because resolver telemetry is used for production safety.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .resolver_evidence import ResolverEvidenceStore

_DEFAULT_MIN_SAMPLES = 30
_DEFAULT_MIN_DISCORDANT = 10
_DEFAULT_ALPHA = 0.05
_DEFAULT_MIN_EFFECT = 0.10
_MAX_RESOLVERS = 16
_Z_95 = 1.959963984540054


def _exact_mcnemar_p_value(a_only: int, b_only: int) -> float:
    discordant = a_only + b_only
    if discordant == 0:
        return 1.0
    smaller = min(a_only, b_only)
    log_two = math.log(2.0)
    tail = 0.0
    for k in range(smaller + 1):
        log_probability = (
            math.lgamma(discordant + 1)
            - math.lgamma(k + 1)
            - math.lgamma(discordant - k + 1)
            - discordant * log_two
        )
        tail += math.exp(log_probability)
    return min(1.0, 2.0 * tail)


def _risk_difference_interval(
    a_only: int,
    b_only: int,
    samples: int,
    min_discordant: int,
) -> tuple[float, float]:
    """Normal approximation for paired risk difference.

    With too few discordant observations the approximation is intentionally
    suppressed. Returning the full interval then prevents a false validation.
    """
    discordant = a_only + b_only
    delta = (a_only - b_only) / samples
    if discordant < min_discordant:
        return -1.0, 1.0
    variance = (discordant - ((a_only - b_only) ** 2) / samples) / (samples ** 2)
    if variance <= 0 or not math.isfinite(variance):
        return delta, delta
    margin = _Z_95 * math.sqrt(variance)
    return max(-1.0, delta - margin), min(1.0, delta + margin)


@dataclass(frozen=True)
class PairwiseValidation:
    resolver_a: str
    resolver_b: str
    paired_samples: int
    a_successes: int
    b_successes: int
    both_successes: int
    a_only: int
    b_only: int
    success_rate_delta: float
    delta_lower_95: float
    delta_upper_95: float
    mcnemar_p_value: float
    statistically_significant: bool
    practically_meaningful: bool
    validated_advantage: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "resolver_a": self.resolver_a,
            "resolver_b": self.resolver_b,
            "paired_samples": self.paired_samples,
            "a_successes": self.a_successes,
            "b_successes": self.b_successes,
            "both_successes": self.both_successes,
            "a_only": self.a_only,
            "b_only": self.b_only,
            "success_rate_delta": self.success_rate_delta,
            "delta_lower_95": self.delta_lower_95,
            "delta_upper_95": self.delta_upper_95,
            "mcnemar_p_value": self.mcnemar_p_value,
            "statistically_significant": self.statistically_significant,
            "practically_meaningful": self.practically_meaningful,
            "validated_advantage": self.validated_advantage,
        }


def validate_pair(
    store: ResolverEvidenceStore,
    resolver_a: str,
    resolver_b: str,
    *,
    platform: str = "unknown",
    media_kind: str = "unknown",
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    min_discordant: int = _DEFAULT_MIN_DISCORDANT,
    alpha: float = _DEFAULT_ALPHA,
    min_effect: float = _DEFAULT_MIN_EFFECT,
) -> PairwiseValidation:
    """Validate A against B using only samples observed for both resolvers."""
    if resolver_a == resolver_b:
        raise ValueError("resolver_a and resolver_b must differ")
    minimum = max(1, int(min_samples))
    discordant_minimum = max(1, int(min_discordant))
    if not 0 < float(alpha) < 1:
        raise ValueError("alpha must be between 0 and 1")
    if not 0 <= float(min_effect) <= 1:
        raise ValueError("min_effect must be between 0 and 1")

    outcomes = store.paired_outcomes(
        resolver_a, resolver_b, platform=platform, media_kind=media_kind
    )
    samples = len(outcomes)
    both_successes = sum(a and b for a, b, _, _ in outcomes)
    a_only = sum(a and not b for a, b, _, _ in outcomes)
    b_only = sum(b and not a for a, b, _, _ in outcomes)
    a_successes = both_successes + a_only
    b_successes = both_successes + b_only
    delta = (a_successes - b_successes) / samples if samples else 0.0
    lower, upper = (
        _risk_difference_interval(a_only, b_only, samples, discordant_minimum)
        if samples else (-1.0, 1.0)
    )
    p_value = _exact_mcnemar_p_value(a_only, b_only)
    enough_samples = samples >= minimum
    enough_discordant = (a_only + b_only) >= discordant_minimum
    significant = enough_samples and enough_discordant and p_value <= float(alpha)
    meaningful = enough_samples and enough_discordant and abs(delta) >= float(min_effect) and lower > 0
    return PairwiseValidation(
        resolver_a=str(resolver_a),
        resolver_b=str(resolver_b),
        paired_samples=samples,
        a_successes=a_successes,
        b_successes=b_successes,
        both_successes=both_successes,
        a_only=a_only,
        b_only=b_only,
        success_rate_delta=delta,
        delta_lower_95=lower,
        delta_upper_95=upper,
        mcnemar_p_value=p_value,
        statistically_significant=significant,
        practically_meaningful=meaningful,
        validated_advantage=significant and meaningful,
    )


def validate_resolver_set(
    store: ResolverEvidenceStore,
    resolvers: Iterable[str],
    *,
    platform: str = "unknown",
    media_kind: str = "unknown",
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    min_discordant: int = _DEFAULT_MIN_DISCORDANT,
    alpha: float = _DEFAULT_ALPHA,
    min_effect: float = _DEFAULT_MIN_EFFECT,
) -> list[dict[str, object]]:
    """Return pairwise validation results without choosing a runtime winner."""
    names = []
    for name in resolvers:
        name = str(name).strip()[:80]
        if name and name not in names:
            names.append(name)
        if len(names) >= _MAX_RESOLVERS:
            break
    results = []
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            results.append(validate_pair(
                store, first, second,
                platform=platform,
                media_kind=media_kind,
                min_samples=min_samples,
                min_discordant=min_discordant,
                alpha=alpha,
                min_effect=min_effect,
            ).as_dict())
    return results
