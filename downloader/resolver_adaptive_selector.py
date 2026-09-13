"""Conservative resolver-level adaptive ordering.

This module only reorders the existing public-media resolver chain. It never
adds, removes, or mutates resolver behavior, and it fails open to the original
order whenever learned evidence is insufficient or invalid.
"""

from __future__ import annotations

from typing import Any, Iterable


DEFAULT_MIN_ATTEMPTS = 20
DEFAULT_MIN_SUCCESS_RATE = 0.70
DEFAULT_MIN_MARGIN = 0.10
ELIGIBLE_RESOLVERS = ("legacy_extractor", "smart_media", "browser_media", "cobalt")


def _row_map(policy: Iterable[dict[str, Any]], minimum_attempts: int) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in policy or ():
        try:
            name = str(row.get("resolver", ""))
            attempts = int(row.get("attempts", 0))
            rate = float(row.get("success_rate", 0.0))
        except (AttributeError, TypeError, ValueError):
            continue
        if name in ELIGIBLE_RESOLVERS and attempts >= minimum_attempts:
            result[name] = {"attempts": attempts, "success_rate": rate}
    return result


def order_resolvers(
    original_order: Iterable[str],
    policy: Iterable[dict[str, Any]],
    *,
    min_attempts: int = DEFAULT_MIN_ATTEMPTS,
    min_success_rate: float = DEFAULT_MIN_SUCCESS_RATE,
    min_margin: float = DEFAULT_MIN_MARGIN,
) -> list[str]:
    """Return a safely reordered resolver list, or the exact original order."""
    original = list(original_order)
    if not original or len(set(original)) != len(original):
        return original
    try:
        minimum_attempts = max(1, int(min_attempts))
        minimum_rate = float(min_success_rate)
        minimum_margin = max(0.0, float(min_margin))
    except (TypeError, ValueError):
        return original
    rows = _row_map(policy, minimum_attempts)
    rows = {
        name: row for name, row in rows.items()
        if 0.0 <= row["success_rate"] <= 1.0
    }
    eligible = [name for name in original if name in rows]
    if len(eligible) < 2:
        return original
    baseline = rows.get(original[0])
    if baseline is None:
        return original
    winner = max(
        eligible,
        key=lambda name: (rows[name]["success_rate"], -original.index(name)),
    )
    if winner == original[0]:
        return original
    winner_rate = rows[winner]["success_rate"]
    baseline_rate = baseline["success_rate"]
    if winner_rate < minimum_rate or winner_rate - baseline_rate < minimum_margin:
        return original
    return [winner] + [name for name in original if name != winner]
