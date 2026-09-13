"""Paired resolver evidence collection for statistically safer adaptation.

This module is runtime-neutral. It records only explicitly supplied resolver
probe outcomes, grouped by one opaque sample id, so later analysis can compare
resolvers on the same request instead of treating fallback positions as
independent samples. It never discovers URLs, invokes resolvers, or changes
runtime ordering.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .smart_learning import SmartTelemetryStore

_MAX_SAMPLE_ID = 80
_MAX_RESOLVER = 80
_MAX_CONTEXT = 40
_MAX_REASON = 240
_DEFAULT_MIN_PAIRED = 20
_ALLOWED_PLATFORMS = frozenset({
    "youtube", "instagram", "facebook", "tiktok", "twitter", "reddit",
    "shahid4u", "telegram", "vimeo", "dailymotion", "other", "unknown",
})
_ALLOWED_MEDIA_KINDS = frozenset({"hls", "dash", "progressive", "iframe", "unknown"})


def _bounded(value: str | None, limit: int, default: str = "unknown") -> str:
    return str(value or default).strip()[:limit]


def _context(value: str | None, allowed: frozenset[str]) -> str:
    value = _bounded(value, _MAX_CONTEXT)
    return value if value in allowed else "other"


def _wilson_lower(successes: int, attempts: int, z: float = 1.96) -> float:
    if attempts <= 0:
        return 0.0
    p = successes / attempts
    denominator = 1.0 + z * z / attempts
    centre = p + z * z / (2.0 * attempts)
    margin = z * math.sqrt((p * (1.0 - p) / attempts) + (z * z / (4.0 * attempts * attempts)))
    return max(0.0, (centre - margin) / denominator)


@dataclass(frozen=True)
class ResolverEvidence:
    resolver: str
    success: bool
    elapsed_ms: float = 0.0
    candidate_count: int = 0
    failure_reason: str | None = None


class ResolverEvidenceStore:
    """Durable paired evidence store; writes are fail-open to callers."""

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
                CREATE TABLE IF NOT EXISTS resolver_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolver TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK(success IN (0,1)),
                    candidate_count INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    failure_reason TEXT,
                    platform TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    UNIQUE(sample_id, resolver)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resolver_evidence_context ON resolver_evidence(platform, media_kind, resolver)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resolver_evidence_sample ON resolver_evidence(sample_id)")
            conn.commit()

    def record_sample(
        self,
        sample_id: str,
        *,
        platform: str,
        media_kind: str,
        outcomes: Iterable[ResolverEvidence | Mapping[str, object]],
    ) -> bool:
        """Record one paired probe sample; rejects malformed/duplicate rows."""
        try:
            sample = _bounded(sample_id, _MAX_SAMPLE_ID, "")
            if not sample:
                return False
            platform_name = _context(platform, _ALLOWED_PLATFORMS)
            media_name = _context(media_kind, _ALLOWED_MEDIA_KINDS)
            rows = []
            for outcome in outcomes:
                if isinstance(outcome, ResolverEvidence):
                    resolver = outcome.resolver
                    success = outcome.success
                    elapsed_ms = outcome.elapsed_ms
                    candidate_count = outcome.candidate_count
                    reason = outcome.failure_reason
                else:
                    resolver = outcome.get("resolver")
                    success = outcome.get("success")
                    elapsed_ms = outcome.get("elapsed_ms", 0.0)
                    candidate_count = outcome.get("candidate_count", 0)
                    reason = outcome.get("failure_reason")
                resolver_name = _bounded(str(resolver or ""), _MAX_RESOLVER, "")
                if not resolver_name:
                    return False
                elapsed = float(elapsed_ms)
                count = int(candidate_count)
                if not math.isfinite(elapsed) or elapsed < 0 or count < 0:
                    return False
                rows.append((sample, resolver_name, int(bool(success)), count, elapsed,
                             _bounded(str(reason), _MAX_REASON) if reason else None,
                             platform_name, media_name))
            if len(rows) < 2 or len({row[1] for row in rows}) != len(rows):
                return False
            now = datetime.now(timezone.utc).isoformat()
            with self._lock, self._connect() as conn:
                conn.executemany("""
                    INSERT INTO resolver_evidence(
                        sample_id,created_at,resolver,success,candidate_count,
                        elapsed_ms,failure_reason,platform,media_kind
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                """, [(r[0], now, r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in rows])
                conn.commit()
            return True
        except (TypeError, ValueError, sqlite3.IntegrityError):
            return False
        except Exception:
            return False

    def paired_policy(
        self,
        *,
        platform: str = "unknown",
        media_kind: str = "unknown",
        min_paired_samples: int = _DEFAULT_MIN_PAIRED,
        max_resolvers: int = 16,
    ) -> list[dict[str, object]]:
        """Return conservative paired evidence metrics, without ranking a winner."""
        minimum = max(1, int(min_paired_samples))
        maximum = max(1, min(int(max_resolvers), 16))
        platform_name = _context(platform, _ALLOWED_PLATFORMS)
        media_name = _context(media_kind, _ALLOWED_MEDIA_KINDS)
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT resolver, COUNT(DISTINCT sample_id), SUM(success), AVG(elapsed_ms)
                FROM resolver_evidence
                WHERE platform = ? AND media_kind = ?
                GROUP BY resolver
                ORDER BY resolver ASC
            """, (platform_name, media_name)).fetchall()
        result = []
        for resolver, samples, successes, avg_ms in rows:
            samples = int(samples)
            successes = int(successes or 0)
            if samples < minimum:
                continue
            result.append({
                "resolver": str(resolver)[:_MAX_RESOLVER],
                "paired_samples": samples,
                "successes": successes,
                "success_rate": successes / samples,
                "success_rate_lower_95": _wilson_lower(successes, samples),
                "avg_elapsed_ms": max(0.0, float(avg_ms or 0.0)),
            })
        result.sort(key=lambda item: (-float(item["success_rate_lower_95"]), float(item["avg_elapsed_ms"]), str(item["resolver"])))
        return result[:maximum]
