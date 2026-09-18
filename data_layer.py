"""Canonical SQLite data access primitives for AliBot.

This module owns connection configuration and small transactional write/read
primitives used by the bot. It intentionally preserves the existing schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


LOCAL_DB_FILE = "bot_stats.db"
VOLUME_DIR = Path("/app/data")
DB_FILE = str(VOLUME_DIR / "bot_stats.db") if VOLUME_DIR.is_dir() else LOCAL_DB_FILE


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def record_download(
    *,
    user_id: int,
    username: str | None,
    url: str,
    website: str,
    media_type: str,
    quality: str,
    created_at: str,
) -> None:
    """Atomically record a delivered download and increment the user counter."""
    conn = get_db()
    try:
        with conn:
            conn.execute(
                """INSERT INTO downloads
                   (user_id, username, url, website, media_type, quality, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, url, website, media_type, quality, created_at),
            )
            conn.execute(
                "UPDATE users SET downloads = downloads + 1 WHERE user_id = ?",
                (user_id,),
            )
    finally:
        conn.close()


def download_counts(*, days: int | None = None) -> dict[str, int]:
    """Return canonical download counters from the existing downloads ledger."""
    conn = get_db()
    try:
        where = ""
        params: tuple[Any, ...] = ()
        if days in {1, 7, 30}:
            where = " WHERE created_at >= ?"
            import datetime as _dt
            cutoff = (_dt.datetime.now() - _dt.timedelta(days=days)).isoformat()
            params = (cutoff,)
        total = conn.execute(f"SELECT COUNT(*) AS n FROM downloads{where}", params).fetchone()["n"]
        videos = conn.execute(
            f"SELECT COUNT(*) AS n FROM downloads{where}" + (" AND media_type = 'video'" if where else " WHERE media_type = 'video'"),
            params,
        ).fetchone()["n"]
        audio = conn.execute(
            f"SELECT COUNT(*) AS n FROM downloads{where}" + (" AND media_type = 'audio'" if where else " WHERE media_type = 'audio'"),
            params,
        ).fetchone()["n"]
        return {"downloads": int(total or 0), "videos": int(videos or 0), "audio": int(audio or 0)}
    finally:
        conn.close()
