"""Read-only unified administrative event model.

This adapter intentionally does not add migrations or change the download path.
It normalizes the existing downloads/error/resolver telemetry into one bounded
snapshot for administrative views. Missing optional tables are handled safely.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class AdminEvent:
    event_id: str
    kind: str
    status: str
    user_id: int | None
    platform: str
    resolver: str | None
    created_at: str | None
    reason: str | None = None


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone())


def recent_events(get_db, limit: int = 50) -> list[dict[str, Any]]:
    """Return a stable, bounded event list from existing persisted telemetry."""
    limit = max(1, min(int(limit), 100))
    conn = get_db()
    try:
        events: list[AdminEvent] = []

        if _table_exists(conn, "downloads"):
            rows = conn.execute(
                "SELECT id, user_id, website, created_at FROM downloads "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            events.extend(
                AdminEvent(
                    event_id=f"download:{int(r['id'])}",
                    kind="download",
                    status="delivered",
                    user_id=int(r["user_id"]) if r["user_id"] is not None else None,
                    platform=str(r["website"] or "unknown"),
                    resolver=None,
                    created_at=str(r["created_at"] or ""),
                )
                for r in rows
            )

        if _table_exists(conn, "error_logs"):
            rows = conn.execute(
                "SELECT id, user_id, website, error_type, error_message, created_at "
                "FROM error_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            events.extend(
                AdminEvent(
                    event_id=f"error:{int(r['id'])}",
                    kind="error",
                    status="failed",
                    user_id=int(r["user_id"]) if r["user_id"] is not None else None,
                    platform=str(r["website"] or "unknown"),
                    resolver=None,
                    created_at=str(r["created_at"] or ""),
                    reason=str(r["error_type"] or r["error_message"] or "")[:300],
                )
                for r in rows
            )

        events.sort(key=lambda e: e.created_at or "", reverse=True)
        return [asdict(event) for event in events[:limit]]
    finally:
        conn.close()


def summary(get_db) -> dict[str, int]:
    """Return conservative counts without inventing success rates."""
    conn = get_db()
    try:
        downloads = int(conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0])
        errors = 0
        if _table_exists(conn, "error_logs"):
            errors = int(conn.execute("SELECT COUNT(*) FROM error_logs").fetchone()[0])
        return {"downloads": downloads, "errors": errors}
    finally:
        conn.close()
