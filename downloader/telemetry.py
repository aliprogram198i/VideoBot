"""Unified, failure-isolated telemetry facade for AliBot.

This module provides one application-facing telemetry contract while keeping
existing SQLite telemetry stores backward compatible. It never stores raw
source URLs or Telegram identifiers.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from download_events import DownloadEvent
from .resolver_contracts import ResolverResult
from .resolver_outcome_telemetry import ResolverOutcomeTelemetry


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _url_key(url: str | None) -> str | None:
    if not url:
        return None
    return hashlib.sha256(str(url).encode("utf-8", "ignore")).hexdigest()


@dataclass(frozen=True)
class TelemetryContext:
    platform: str = "unknown"
    media_kind: str = "unknown"


class TelemetryRecorder:
    """Single facade for resolver and downstream download outcome telemetry."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self._resolver = ResolverOutcomeTelemetry(db_path)
        self.db_path = Path(self._resolver.db_path)
        self._lock = threading.RLock()
        self._initialize_download_outcomes()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize_download_outcomes(self) -> None:
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS download_outcomes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        attempt_id TEXT,
                        platform TEXT NOT NULL,
                        media_type TEXT NOT NULL,
                        success INTEGER NOT NULL CHECK(success IN (0,1)),
                        elapsed_ms REAL NOT NULL,
                        selected_url_key TEXT,
                        failure_reason TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_download_outcomes_created "
                    "ON download_outcomes(created_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_download_outcomes_platform "
                    "ON download_outcomes(platform, media_type)"
                )
                conn.commit()
        except Exception:
            # Telemetry must never become a runtime dependency.
            return

    def record_resolver(
        self,
        result: ResolverResult,
        *,
        context: TelemetryContext | None = None,
    ) -> None:
        """Persist a normalized resolver result without affecting control flow."""
        try:
            if not isinstance(result, ResolverResult):
                raise TypeError("result must be a ResolverResult")
            context = context or TelemetryContext()
            self._resolver.record(
                result.resolver,
                success=result.ok,
                candidate_count=result.candidate_count,
                elapsed_ms=result.elapsed_ms,
                failure_reason=result.failure_reason,
                platform=context.platform,
                media_kind=context.media_kind,
            )
        except Exception:
            return

    def record_download_event(self, event: DownloadEvent) -> None:
        """Record one canonical download outcome without affecting control flow."""
        try:
            if not isinstance(event, DownloadEvent):
                raise TypeError("event must be a DownloadEvent")
            self.record_download(
                platform=event.website,
                media_type=event.media_type,
                success=event.success,
                elapsed_ms=event.elapsed_ms or 0.0,
                url=event.url,
                attempt_id=event.attempt_id,
                failure_reason=event.failure_reason,
            )
        except Exception:
            return

    def record_download(
        self,
        *,
        platform: str,
        media_type: str,
        success: bool,
        elapsed_ms: float,
        url: str | None = None,
        attempt_id: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        """Record a downstream outcome; raw URLs are reduced to SHA-256 keys."""
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO download_outcomes(
                        created_at,attempt_id,platform,media_type,success,
                        elapsed_ms,selected_url_key,failure_reason
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        _utc_now(),
                        str(attempt_id)[:128] if attempt_id else None,
                        str(platform or "unknown")[:40],
                        str(media_type or "unknown")[:40],
                        int(bool(success)),
                        max(0.0, float(elapsed_ms)),
                        _url_key(url),
                        str(failure_reason)[:500] if failure_reason else None,
                    ),
                )
                conn.commit()
        except Exception:
            return

    def download_summary(self) -> list[dict[str, Any]]:
        """Return aggregate downstream outcomes for diagnostics/admin use."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT platform, media_type, COUNT(*) AS attempts,
                           SUM(success) AS successes,
                           AVG(elapsed_ms) AS avg_elapsed_ms
                    FROM download_outcomes
                    GROUP BY platform, media_type
                    ORDER BY attempts DESC, platform ASC, media_type ASC
                    """
                ).fetchall()
            return [
                {
                    "platform": row[0],
                    "media_type": row[1],
                    "attempts": int(row[2]),
                    "successes": int(row[3] or 0),
                    "success_rate": (int(row[3] or 0) / int(row[2]))
                    if row[2]
                    else 0.0,
                    "avg_elapsed_ms": float(row[4] or 0.0),
                }
                for row in rows
            ]
        except Exception:
            return []


__all__ = ["TelemetryContext", "TelemetryRecorder"]
