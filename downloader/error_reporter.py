"""Deterministic, non-AI failure reporting for AliBot.

Captures structured application log failures and exposes a compact root-cause
classification for the admin dashboard. Secrets and query strings are never
stored.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse


_PATTERNS = [
    ("YOINKU_422", re.compile(r"Yoinku HTTP failure.*(?:status:\s*)?422", re.I), "Yoinku rejected the request (HTTP 422). The URL/platform payload is not accepted by the provider."),
    ("HTTP_401", re.compile(r"(?:HTTP|status)\s*401|Unauthorized", re.I), "The source requires authentication or rejected anonymous access."),
    ("HTTP_403", re.compile(r"(?:HTTP|status)\s*403|Forbidden", re.I), "The source blocked access (HTTP 403)."),
    ("HTTP_404", re.compile(r"(?:HTTP|status)\s*404|Not Found", re.I), "The requested resource does not exist or is no longer available."),
    ("NO_CANDIDATES", re.compile(r"no candidates|produced no candidates", re.I), "No downloadable media candidate was discovered from the source page."),
    ("LOGIN_REQUIRED", re.compile(r"sign in|login|log in|authentication required|not a bot", re.I), "The platform requires authentication or bot verification."),
    ("TIMEOUT", re.compile(r"timeout|timed out", re.I), "The download/extraction operation exceeded its time limit."),
    ("TELEGRAM_SIZE", re.compile(r"entity too large|request entity too large|file is too big", re.I), "The resulting file exceeds Telegram's upload limit."),
]


def _safe_url(text: str) -> str | None:
    for token in re.findall(r"https?://[^\s\]\[)>'\"]+", text or ""):
        try:
            p = urlparse(token.rstrip(".,;"))
            if p.hostname:
                return urlunparse((p.scheme, p.hostname, p.path, "", "", ""))
        except Exception:
            pass
    return None


def classify(message: str) -> tuple[str, str]:
    for code, pattern, explanation in _PATTERNS:
        if pattern.search(message or ""):
            return code, explanation
    return "UNKNOWN", "The operation failed without a recognized deterministic root-cause signature."


def _db_path() -> str:
    volume = Path("/app/data")
    return str(volume / "bot_stats.db") if volume.is_dir() else "bot_stats.db"


def ensure_table() -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS error_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            level TEXT NOT NULL,
            code TEXT NOT NULL,
            reason TEXT NOT NULL,
            message TEXT NOT NULL,
            url TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_error_events_created_at ON error_events(created_at DESC)")
    conn.commit()
    conn.close()


def record(level: str, message: str) -> None:
    if not message:
        return
    code, reason = classify(message)
    # Avoid recursive logging: this function only writes to SQLite.
    try:
        conn = sqlite3.connect(_db_path())
        conn.execute(
            "INSERT INTO error_events(created_at,level,code,reason,message,url) VALUES(?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), level, code, reason, message[:2000], _safe_url(message)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


class ErrorCaptureHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        try:
            record_message = self.format(record)
            record_event(record.levelname, record_message)
        except Exception:
            pass


def record_event(level: str, message: str) -> None:
    record(level, message)


def install() -> None:
    ensure_table()
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_alibot_error_capture", False):
            return
    handler = ErrorCaptureHandler()
    handler._alibot_error_capture = True
    handler.setLevel(logging.WARNING)
    root.addHandler(handler)


def latest(limit: int = 5) -> list[sqlite3.Row]:
    ensure_table()
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM error_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 20)),)).fetchall()
    conn.close()
    return rows


def summary() -> dict:
    rows = latest(1)
    if not rows:
        return {"count": 0, "latest": None}
    row = rows[0]
    return {"count": len(latest(20)), "latest": dict(row)}
