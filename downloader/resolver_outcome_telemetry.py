"""Bounded resolver outcome telemetry for adaptive orchestration.

Stores resolver-level technical outcomes without raw source URLs or Telegram
identifiers. Context is reduced to bounded platform and media-kind classes.
The data is advisory only: recording failures must never alter resolver or
fallback behavior.
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
_MAX_CONTEXT = 40
_DEFAULT_MIN_ATTEMPTS = 20
_DEFAULT_MAX_RESOLVERS = 16
_ALLOWED_PLATFORMS = frozenset({"youtube", "instagram", "facebook", "tiktok", "twitter", "reddit", "shahid4u", "telegram", "vimeo", "dailymotion", "other", "unknown"})
_ALLOWED_MEDIA_KINDS = frozenset({"hls", "dash", "progressive", "iframe", "unknown"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_context(value: str | None, allowed: frozenset[str]) -> str:
    normalized = str(value or "unknown").strip().lower()[:_MAX_CONTEXT]
    return normalized if normalized in allowed else "other"


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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resolver_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    resolver TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK(success IN (0,1)),
                    candidate_count INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    failure_reason TEXT,
                    platform TEXT NOT NULL DEFAULT 'unknown',
                    media_kind TEXT NOT NULL DEFAULT 'unknown'
                )
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(resolver_outcomes)")}
            if "platform" not in columns:
                conn.execute("ALTER TABLE resolver_outcomes ADD COLUMN platform TEXT NOT NULL DEFAULT 'unknown'")
            if "media_kind" not in columns:
                conn.execute("ALTER TABLE resolver_outcomes ADD COLUMN media_kind TEXT NOT NULL DEFAULT 'unknown'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resolver_outcomes_resolver ON resolver_outcomes(resolver)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resolver_outcomes_created ON resolver_outcomes(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resolver_outcomes_context ON resolver_outcomes(platform, media_kind, resolver)")
            conn.commit()

    def record(self, resolver: str, *, success: bool, candidate_count: int = 0,
               elapsed_ms: float = 0.0, failure_reason: str | None = None,
               platform: str = "unknown", media_kind: str = "unknown") -> None:
        """Record one bounded technical outcome; never raises to callers."""
        try:
            resolver_name = str(resolver)[:_MAX_RESOLVER]
            reason = str(failure_reason)[:_MAX_REASON] if failure_reason else None
            platform_name = _bounded_context(platform, _ALLOWED_PLATFORMS)
            media_kind_name = _bounded_context(media_kind, _ALLOWED_MEDIA_KINDS)
            with self._lock, self._connect() as conn:
                conn.execute("""
                    INSERT INTO resolver_outcomes(
                        created_at,resolver,success,candidate_count,elapsed_ms,
                        failure_reason,platform,media_kind
                    ) VALUES(?,?,?,?,?,?,?,?)
                """, (_utc_now(), resolver_name, int(bool(success)),
                       max(0, int(candidate_count)), max(0.0, float(elapsed_ms)),
                       reason, platform_name, media_kind_name))
                conn.commit()
        except Exception:
            return

    def summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT resolver, COUNT(*) AS attempts, SUM(success) AS successes,
                       AVG(elapsed_ms) AS avg_elapsed_ms
                FROM resolver_outcomes GROUP BY resolver
                ORDER BY attempts DESC, resolver ASC
            """).fetchall()
        return [{"resolver": row[0], "attempts": int(row[1]),
                 "successes": int(row[2] or 0),
                 "success_rate": (int(row[2] or 0) / int(row[1])) if row[1] else 0.0,
                 "avg_elapsed_ms": float(row[3] or 0.0)} for row in rows]

    def resolver_policy(self, *, min_attempts: int = _DEFAULT_MIN_ATTEMPTS,
                        max_resolvers: int = _DEFAULT_MAX_RESOLVERS) -> list[dict[str, Any]]:
        """Return a conservative global ranking from observed outcomes."""
        return self._policy_where(None, None, min_attempts=min_attempts, max_resolvers=max_resolvers)

    def platform_policy(self, *, platform: str = "unknown",
                        min_attempts: int = _DEFAULT_MIN_ATTEMPTS,
                        max_resolvers: int = _DEFAULT_MAX_RESOLVERS) -> list[dict[str, Any]]:
        """Rank resolvers across all media kinds for one bounded platform class."""
        platform_name = _bounded_context(platform, _ALLOWED_PLATFORMS)
        return self._policy_where(platform_name, None, min_attempts=min_attempts, max_resolvers=max_resolvers)

    def contextual_policy(self, *, platform: str = "unknown", media_kind: str = "unknown",
                          min_attempts: int = _DEFAULT_MIN_ATTEMPTS,
                          max_resolvers: int = _DEFAULT_MAX_RESOLVERS) -> list[dict[str, Any]]:
        """Rank resolvers only within a bounded, sanitized context."""
        platform_name = _bounded_context(platform, _ALLOWED_PLATFORMS)
        media_kind_name = _bounded_context(media_kind, _ALLOWED_MEDIA_KINDS)
        return self._policy_where(platform_name, media_kind_name, min_attempts=min_attempts, max_resolvers=max_resolvers)

    def _policy_where(self, platform: str | None, media_kind: str | None, *,
                      min_attempts: int, max_resolvers: int) -> list[dict[str, Any]]:
        min_attempts = max(1, int(min_attempts))
        max_resolvers = max(1, min(int(max_resolvers), _DEFAULT_MAX_RESOLVERS))
        where = []
        params: list[Any] = []
        if platform is not None:
            where.append("platform = ?")
            params.append(platform)
        if media_kind is not None:
            where.append("media_kind = ?")
            params.append(media_kind)
        predicate = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT resolver, COUNT(*) AS attempts, SUM(success) AS successes,
                       AVG(elapsed_ms) AS avg_elapsed_ms
                FROM resolver_outcomes
                {predicate}
                GROUP BY resolver HAVING COUNT(*) >= ?
            """, (*params, min_attempts)).fetchall()
        ranked = []
        for row in rows:
            attempts, successes = int(row[1]), int(row[2] or 0)
            item = {"resolver": str(row[0])[:_MAX_RESOLVER], "attempts": attempts,
                    "successes": successes, "success_rate": successes / attempts if attempts else 0.0,
                    "avg_elapsed_ms": max(0.0, float(row[3] or 0.0))}
            if platform is not None:
                item["platform"] = platform
            if media_kind is not None:
                item["media_kind"] = media_kind
            ranked.append(item)
        ranked.sort(key=lambda item: (-item["success_rate"], item["avg_elapsed_ms"], -item["attempts"], item["resolver"]))
        return ranked[:max_resolvers]
