"""Evidence-gated resolver selection policy.

This module is deliberately runtime-neutral. It consumes pairwise statistical
validation results and may propose a new first resolver only when one
resolver has a validated advantage over every other eligible resolver in the
same context. Multiple-comparison control is applied with Holm's step-down
method. No resolver is invoked and no runtime chain is mutated here.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from .resolver_adaptive_selector import ELIGIBLE_RESOLVERS

_DEFAULT_ALPHA = 0.05
_DEFAULT_MIN_SAMPLES = 30
_DEFAULT_MIN_DISCORDANT = 10
_DEFAULT_MIN_EFFECT = 0.10
_MAX_RESOLVERS = 16


def _names(original_order: Iterable[str]) -> list[str]:
    names: list[str] = []
    for raw in original_order:
        name = str(raw).strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= _MAX_RESOLVERS:
            break
    return names


def _candidate_pair_result(
    candidate: str,
    other: str,
    validations: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return a directional result with candidate treated as resolver A."""
    for raw in validations:
        if not isinstance(raw, dict):
            continue
        first = str(raw.get("resolver_a", ""))
        second = str(raw.get("resolver_b", ""))
        if (first, second) == (candidate, other):
            return raw
        if (first, second) == (other, candidate):
            try:
                return {
                    **raw,
                    "resolver_a": candidate,
                    "resolver_b": other,
                    "success_rate_delta": -float(raw.get("success_rate_delta", 0.0)),
                    "delta_lower_95": -float(raw.get("delta_upper_95", 0.0)),
                    "delta_upper_95": -float(raw.get("delta_lower_95", 0.0)),
                }
            except (TypeError, ValueError):
                return None
    return None


def _passes_directional_gates(
    result: dict[str, Any],
    *,
    min_samples: int,
    min_discordant: int,
    min_effect: float,
) -> bool:
    try:
        samples = int(result.get("paired_samples", 0))
        a_only = int(result.get("a_only", 0))
        b_only = int(result.get("b_only", 0))
        delta = float(result.get("success_rate_delta", 0.0))
        lower = float(result.get("delta_lower_95", -1.0))
        p_value = float(result.get("mcnemar_p_value", 1.0))
    except (TypeError, ValueError):
        return False
    discordant = a_only + b_only
    return (
        samples >= min_samples
        and discordant >= min_discordant
        and math.isfinite(delta)
        and math.isfinite(lower)
        and math.isfinite(p_value)
        and delta >= min_effect
        and lower > 0.0
        and 0.0 <= p_value <= 1.0
    )


def _holm_significant(p_values: list[float], alpha: float) -> bool:
    """Require every candidate-vs-peer test to pass Holm step-down control."""
    if not p_values:
        return False
    ordered = sorted(p_values)
    total = len(ordered)
    for index, p_value in enumerate(ordered):
        threshold = alpha / (total - index)
        if p_value > threshold:
            return False
    return True


def choose_validated_first(
    original_order: Iterable[str],
    validations: Iterable[dict[str, Any]],
    *,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    min_discordant: int = _DEFAULT_MIN_DISCORDANT,
    alpha: float = _DEFAULT_ALPHA,
    min_effect: float = _DEFAULT_MIN_EFFECT,
) -> list[str]:
    """Return a reordered chain only when one candidate clears every gate.

    The exact original list is returned on malformed input, insufficient
    evidence, ambiguity, or multiple candidates. The function never adds or
    removes resolvers.
    """
    original = list(original_order)
    if not original or len(set(original)) != len(original):
        return original
    try:
        minimum_samples = max(1, int(min_samples))
        minimum_discordant = max(1, int(min_discordant))
        significance = float(alpha)
        minimum_effect = float(min_effect)
    except (TypeError, ValueError):
        return original
    if not 0.0 < significance < 1.0 or not 0.0 <= minimum_effect <= 1.0:
        return original

    names = [name for name in _names(original) if name in ELIGIBLE_RESOLVERS]
    if len(names) < 2:
        return original

    validation_rows = list(validations or ())
    winners: list[str] = []
    for candidate in names:
        p_values: list[float] = []
        valid = True
        for other in names:
            if other == candidate:
                continue
            result = _candidate_pair_result(candidate, other, validation_rows)
            if result is None or not _passes_directional_gates(
                result,
                min_samples=minimum_samples,
                min_discordant=minimum_discordant,
                min_effect=minimum_effect,
            ):
                valid = False
                break
            p_values.append(float(result["mcnemar_p_value"]))
        if valid and _holm_significant(p_values, significance):
            winners.append(candidate)

    if len(winners) != 1:
        return original
    winner = winners[0]
    if winner == original[0]:
        return original
    return [winner] + [name for name in original if name != winner]


__all__ = ["choose_validated_first"]
