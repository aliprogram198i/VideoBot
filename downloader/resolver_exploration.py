"""Bounded exploration primitives for unbiased resolver evidence.

This module is intentionally runtime-neutral. It does not alter the current
resolver chain by itself. A caller must explicitly opt in with a non-zero
exploration rate and supply an RNG. The default rate is zero.
"""

from __future__ import annotations

import random
from typing import Iterable, Sequence

DEFAULT_EXPLORATION_RATE = 0.0
MAX_EXPLORATION_RATE = 0.05


def normalized_rate(value: float | int | str | None) -> float:
    """Return a bounded exploration rate; invalid values fail closed to zero."""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return DEFAULT_EXPLORATION_RATE
    if rate < 0.0:
        return DEFAULT_EXPLORATION_RATE
    return min(rate, MAX_EXPLORATION_RATE)


def should_explore(rate: float | int | str | None, *, draw: float) -> bool:
    """Decide whether one request enters an exploration sample."""
    probability = normalized_rate(rate)
    try:
        sample = float(draw)
    except (TypeError, ValueError):
        return False
    if not 0.0 <= sample < 1.0:
        return False
    return probability > 0.0 and sample < probability


def exploratory_order(
    original_order: Sequence[str] | Iterable[str],
    *,
    rng: random.Random | random.SystemRandom | None = None,
) -> list[str]:
    """Return a random permutation without adding/removing resolver names."""
    original = list(original_order)
    if len(original) < 2 or len(set(original)) != len(original):
        return original
    shuffled = list(original)
    (rng or random.SystemRandom()).shuffle(shuffled)
    return shuffled
