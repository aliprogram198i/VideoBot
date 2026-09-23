"""Resolver outcome telemetry for AliBot download attempts.

The module is intentionally independent from bot.py so telemetry cannot become
part of the resolver critical path. Database write failures are swallowed and
reported to the logger.
"""

from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

logger = logging.getLogger(__name__)

_attempt_context: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("alibot_resolver_attempt", default=None)
)


def ensure_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resolver_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id TEXT NOT NULL,
            website TEXT NOT NULL,
            media_type TEXT NOT NULL,
            resolver TEXT NOT NULL,
            event_type TEXT NOT NULL,
            error_type TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resolver_events_attempt "
        "ON resolver_events(attempt_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resolver_events_created "
        "ON resolver_events(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resolver_events_resolver "
        "ON resolver_events(resolver)"
    )
    conn.commit()


def begin_attempt(attempt_id: str, website: str, media_type: str) -> None:
    _attempt_context.set(
        {
            "attempt_id": attempt_id,
            "website": website or "unknown",
            "media_type": media_type or "unknown",
            "final_resolver": None,
        }
    )


def set_final_resolver(resolver: str | None) -> None:
    state = _attempt_context.get()
    if state is not None:
        state["final_resolver"] = (resolver or "unknown").strip() or "unknown"


def clear_attempt() -> None:
    _attempt_context.set(None)


def _state() -> dict[str, Any] | None:
    return _attempt_context.get()


def _write_event(
    get_db: Callable[[], Any],
    *,
    resolver: str,
    event_type: str,
    error_type: str | None = None,
    attempt_id: str | None = None,
    website: str | None = None,
    media_type: str | None = None,
) -> None:
    state = _state()
    resolved_attempt_id = attempt_id or (state or {}).get("attempt_id")
    if not resolved_attempt_id:
        return

    try:
        conn = get_db()
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO resolver_events (
                attempt_id, website, media_type, resolver,
                event_type, error_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_attempt_id,
                website or (state or {}).get("website") or "unknown",
                media_type or (state or {}).get("media_type") or "unknown",
                resolver or "unknown",
                event_type,
                error_type,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning(
            "Resolver telemetry write skipped: %s",
            type(exc).__name__,
        )


def record_error(
    get_db: Callable[[], Any],
    *,
    resolver: str | None,
    error_type: str | None = None,
    attempt_id: str | None = None,
    website: str | None = None,
    media_type: str | None = None,
) -> None:
    _write_event(
        get_db,
        resolver=resolver or "unknown",
        event_type="error",
        error_type=error_type,
        attempt_id=attempt_id,
        website=website,
        media_type=media_type,
    )


def record_terminal_failure(
    get_db: Callable[[], Any],
    *,
    resolver: str | None = None,
    attempt_id: str | None = None,
    website: str | None = None,
    media_type: str | None = None,
) -> None:
    state = _state() or {}
    _write_event(
        get_db,
        resolver=resolver or state.get("final_resolver") or "download_pipeline",
        event_type="terminal_failure",
        attempt_id=attempt_id,
        website=website,
        media_type=media_type,
    )


def record_success(
    get_db: Callable[[], Any],
    *,
    website: str,
    media_type: str,
    resolver: str | None = None,
) -> None:
    state = _state() or {}
    _write_event(
        get_db,
        resolver=resolver or state.get("final_resolver") or "unknown",
        event_type="success",
        website=website,
        media_type=media_type,
    )


def get_monitor_data(get_db: Callable[[], Any], days: int = 1) -> dict[str, Any]:
    days = max(1, int(days))
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT id, attempt_id, website, media_type, resolver,
                   event_type, error_type, created_at
            FROM resolver_events
            WHERE created_at >= ?
            ORDER BY id ASC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    attempts: dict[str, list[Any]] = {}
    for row in rows:
        attempts.setdefault(row["attempt_id"], []).append(row)

    successful = sum(
        1 for events in attempts.values()
        if any(row["event_type"] == "success" for row in events)
    )
    terminal_failed = sum(
        1 for events in attempts.values()
        if any(row["event_type"] == "terminal_failure" for row in events)
        and not any(row["event_type"] == "success" for row in events)
    )

    by_resolver: dict[str, dict[str, Any]] = {}
    recovery: dict[str, int] = {}

    for attempt_id, events in attempts.items():
        had_error = any(row["event_type"] == "error" for row in events)
        success_rows = [row for row in events if row["event_type"] == "success"]
        success_resolvers = {row["resolver"] for row in success_rows}

        for row in events:
            resolver = row["resolver"] or "unknown"
            stats = by_resolver.setdefault(
                resolver,
                {
                    "attempt_ids": set(),
                    "attempts": 0,
                    "errors": 0,
                    "recovered": 0,
                    "terminal_failures": 0,
                },
            )
            if row["event_type"] in {"error", "success"}:
                stats["attempt_ids"].add(attempt_id)
            if row["event_type"] == "error":
                stats["errors"] += 1
            if row["event_type"] == "terminal_failure":
                stats["terminal_failures"] += 1

        if had_error:
            for resolver in success_resolvers:
                by_resolver.setdefault(
                    resolver,
                    {
                        "attempt_ids": set(),
                        "attempts": 0,
                        "errors": 0,
                        "recovered": 0,
                        "terminal_failures": 0,
                    },
                )["recovered"] += 1
            error_resolvers = []
            for row in events:
                if row["event_type"] == "error":
                    resolver = row["resolver"] or "unknown"
                    if not error_resolvers or error_resolvers[-1] != resolver:
                        error_resolvers.append(resolver)
            for success_resolver in success_resolvers:
                for error_resolver in error_resolvers:
                    key = f"{error_resolver} → {success_resolver}"
                    recovery[key] = recovery.get(key, 0) + 1

    resolver_rows = []
    for resolver, stats in sorted(
        by_resolver.items(),
        key=lambda item: (
            -(item[1]["errors"] + item[1]["recovered"]),
            item[0],
        ),
    ):
        stats["attempts"] = len(stats.pop("attempt_ids", set()))
        resolver_rows.append({"resolver": resolver, **stats})

    return {
        "period_days": days,
        "total_attempts": len(attempts),
        "successful": successful,
        "terminal_failures": terminal_failed,
        "resolver_outcomes": resolver_rows,
        "recovery": [
            {"transition": key, "count": count}
            for key, count in sorted(recovery.items(), key=lambda item: -item[1])
        ],
    }
