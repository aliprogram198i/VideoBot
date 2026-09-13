"""Dormant shadow evaluation for evidence-gated resolver selection.

This layer evaluates what the validated selection policy *would* choose, then
records only a bounded decision summary. It never changes resolver order,
invokes a resolver, discovers media, or creates network requests.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .resolver_selection_policy import choose_validated_first
from .smart_learning import SmartTelemetryStore

_MAX_ORDER = 16
_MAX_NAME = 80
_MAX_CONTEXT = 40
_MAX_REASON = 120
_ALLOWED_PLATFORMS = frozenset({
    "youtube", "instagram", "facebook", "tiktok", "twitter", "reddit",
    "shahid4u", "telegram", "vimeo", "dailymotion", "other", "unknown",
})
_ALLOWED_MEDIA_KINDS = frozenset({"hls", "dash", "progressive", "iframe", "unknown"})


def _context(value: str | None, allowed: frozenset[str]) -> str:
    normalized = str(value or "unknown").strip().lower()[:_MAX_CONTEXT]
    return normalized if normalized in allowed else "other"


def _order(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        name = str(raw).strip()[:_MAX_NAME]
        if name and name not in result:
            result.append(name)
        if len(result) >= _MAX_ORDER:
            break
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ShadowDecision:
    platform: str
    media_kind: str
    original_order: tuple[str, ...]
    proposed_order: tuple[str, ...]
    would_reorder: bool
    proposed_first: str | None

    @property
    def changed(self) -> bool:
        return self.would_reorder

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "media_kind": self.media_kind,
            "original_order": list(self.original_order),
            "proposed_order": list(self.proposed_order),
            "would_reorder": self.would_reorder,
            "proposed_first": self.proposed_first,
        }


def evaluate_shadow(
    original_order: Iterable[str],
    validations: Iterable[Mapping[str, Any]],
    *,
    platform: str = "unknown",
    media_kind: str = "unknown",
    min_samples: int = 30,
    min_discordant: int = 10,
    alpha: float = 0.05,
    min_effect: float = 0.10,
) -> ShadowDecision:
    """Evaluate the dormant policy without mutating runtime state."""
    original = _order(original_order)
    platform_name = _context(platform, _ALLOWED_PLATFORMS)
    media_name = _context(media_kind, _ALLOWED_MEDIA_KINDS)
    try:
        proposed = _order(choose_validated_first(
            original,
            validations,
            min_samples=min_samples,
            min_discordant=min_discordant,
            alpha=alpha,
            min_effect=min_effect,
        ))
    except Exception:
        proposed = list(original)
    if len(proposed) != len(original) or set(proposed) != set(original):
        proposed = list(original)
    return ShadowDecision(
        platform=platform_name,
        media_kind=media_name,
        original_order=tuple(original),
        proposed_order=tuple(proposed),
        would_reorder=proposed != original,
        proposed_first=proposed[0] if proposed else None,
    )


class ResolverShadowDecisionStore:
    """Bounded durable store for hypothetical selection decisions."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        if path is None:
            path = SmartTelemetryStore().db_path
        self.db_path = Path(path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resolver_shadow_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    original_order TEXT NOT NULL,
                    proposed_order TEXT NOT NULL,
                    would_reorder INTEGER NOT NULL CHECK(would_reorder IN (0,1)),
                    proposed_first TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_context ON resolver_shadow_decisions(platform, media_kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_created ON resolver_shadow_decisions(created_at)")
            conn.commit()

    def record(self, decision: ShadowDecision) -> bool:
        """Persist one sanitized decision; telemetry failures never escape."""
        try:
            original = _order(decision.original_order)
            proposed = _order(decision.proposed_order)
            if len(original) != len(proposed) or set(original) != set(proposed):
                return False
            platform_name = _context(decision.platform, _ALLOWED_PLATFORMS)
            media_name = _context(decision.media_kind, _ALLOWED_MEDIA_KINDS)
            first = proposed[0] if proposed else None
            with self._lock, self._connect() as conn:
                conn.execute("""
                    INSERT INTO resolver_shadow_decisions(
                        created_at, platform, media_kind, original_order,
                        proposed_order, would_reorder, proposed_first
                    ) VALUES(?,?,?,?,?,?,?)
                """, (
                    _utc_now(), platform_name, media_name,
                    json.dumps(original, separators=(",", ":"), ensure_ascii=True),
                    json.dumps(proposed, separators=(",", ":"), ensure_ascii=True),
                    int(proposed != original), first,
                ))
                conn.commit()
            return True
        except Exception:
            return False

    def summary(self, *, platform: str = "unknown", media_kind: str = "unknown") -> dict[str, Any]:
        """Return bounded aggregate shadow metrics for one context."""
        platform_name = _context(platform, _ALLOWED_PLATFORMS)
        media_name = _context(media_kind, _ALLOWED_MEDIA_KINDS)
        with self._connect() as conn:
            total, reordered = conn.execute("""
                SELECT COUNT(*), COALESCE(SUM(would_reorder), 0)
                FROM resolver_shadow_decisions
                WHERE platform = ? AND media_kind = ?
            """, (platform_name, media_name)).fetchone()
            first_rows = conn.execute("""
                SELECT proposed_first, COUNT(*)
                FROM resolver_shadow_decisions
                WHERE platform = ? AND media_kind = ?
                  AND proposed_first IS NOT NULL
                GROUP BY proposed_first
                ORDER BY COUNT(*) DESC, proposed_first ASC
                LIMIT 16
            """, (platform_name, media_name)).fetchall()
        total = int(total or 0)
        reordered = int(reordered or 0)
        return {
            "platform": platform_name,
            "media_kind": media_name,
            "decisions": total,
            "would_reorder": reordered,
            "reorder_rate": (reordered / total) if total else 0.0,
            "proposed_first": [
                {"resolver": str(name)[:_MAX_NAME], "count": int(count)}
                for name, count in first_rows
            ],
        }


__all__ = ["ResolverShadowDecisionStore", "ShadowDecision", "evaluate_shadow"]
