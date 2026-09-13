"""Bounded resolver outcome telemetry for adaptive orchestration.

Stores resolver-level technical outcomes without raw source URLs or Telegram
identifiers. The data is advisory only: recording failures must never alter the
existing resolver/fallback behavior.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .smart_learning import SmartTelemetryStore

_MAX_REASON = 500
_MAX_RESOLVER = 80
_DEFAULT_MIN_ATTEMPTS = 20
_DEFAULT_MAX_RESOLVERS = 16


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResolverOutcomeTelemetry:
    """Durable, low-volume resolver outcome store."""

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resolver_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    resolver TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK(success IN (0,1)),
                    candidate_count INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    failure_reason TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resolver_outcomes_resolver ON resolver_outcomes(resolver)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resolver_outcomes_created ON resolver_outcomes(created_at)"
            )
            conn.commit()

    def record(
        self,
        resolver: str,
        *,
        success: bool,
        candidate_count: int = 0,
        elapsed_ms: float = 0.0,
        failure_reason: str | None = None,
    ) -> None:
        """Record one bounded technical outcome; never raises to callers."""
        try:
            resolver_name = str(resolver)[:_MAX_RESOLVER]
            reason = str(failure_reason)[:_MAX_REASON] if failure_reason else None
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO resolver_outcomes(
                        created_at,resolver,success,candidate_count,elapsed_ms,failure_reason
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        _utc_now(),
                        resolver_name,
                        int(bool(success)),
                        max(0, int(candidate_count)),
                        max(0.0, float(elapsed_ms)),
                        reason,
                    ),
                )
                conn.commit()
        except Exception:
            # Telemetry is strictly non-blocking and cannot affect downloads.
            return

    def summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT resolver, COUNT(*) AS attempts,
                       SUM(success) AS successes,
                       AVG(elapsed_ms) AS avg_elapsed_ms
                FROM resolver_outcomes
                GROUP BY resolver
                ORDER BY attempts DESC, resolver ASC
                """
            ).fetchall()
        return [
            {
                "resolver": row[0],
                "attempts": int(row[1]),
                "successes": int(row[2] or 0),
                "success_rate": (int(row[2] or 0) / int(row[1])) if row[1] else 0.0,
                "avg_elapsed_ms": float(row[3] or 0.0),
            }
            for row in rows
        ]

    def resolver_policy(
        self,
        *,
        min_attempts: int = _DEFAULT_MIN_ATTEMPTS,
        max_resolvers: int = _DEFAULT_MAX_RESOLVERS,
    ) -> list[dict[str, Any]]:
        """Return a conservative ranking from observed resolver outcomes.

        Only resolvers with enough observations are ranked. Success rate is the
        primary signal; latency is a secondary tie-breaker and never overrides a
        materially better success rate. Unknown or under-sampled resolvers are
        intentionally omitted so callers can keep their existing safe order.
        """
        min_attempts = max(1, int(min_attempts))
        max_resolvers = max(1, min(int(max_resolvers), _DEFAULT_MAX_RESOLVERS))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT resolver,
                       COUNT(*) AS attempts,
                       SUM(success) AS successes,
                       AVG(elapsed_ms) AS avg_elapsed_ms
                FROM resolver_outcomes
                GROUP BY resolver
                HAVING COUNT(*) >= ?
                """,
                (min_attempts,),
            ).fetchall()

        ranked: list[dict[str, Any]] = []
        for row in rows:
            attempts = int(row[1])
            successes = int(row[2] or 0)
            success_rate = successes / attempts if attempts else 0.0
            avg_elapsed_ms = max(0.0, float(row[3] or 0.0))
            ranked.append(
                {
                    "resolver": str(row[0])[:_MAX_RESOLVER],
                    "attempts": attempts,
                    "successes": successes,
                    "success_rate": success_rate,
                    "avg_elapsed_ms": avg_elapsed_ms,
                }
            )

        ranked.sort(
            key=lambda item: (
                -item["success_rate"],
                item["avg_elapsed_ms"],
                -item["attempts"],
                item["resolver"],
            )
        )
        return ranked[:max_resolvers]
