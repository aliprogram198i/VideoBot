"""Canonical SQLite data access primitives for AliBot.

This module owns connection configuration and small transactional write/read
primitives used by the bot. It intentionally preserves the existing schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from download_events import DownloadEvent


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
    event: DownloadEvent | None = None,
    user_id: int | None = None,
    username: str | None = None,
    url: str | None = None,
    website: str | None = None,
    media_type: str | None = None,
    quality: str | None = None,
    created_at: str | None = None,
) -> None:
    """Atomically record one canonical successful-delivery event.

    ``event`` is the preferred path. The legacy keyword fields remain
    supported so existing callers keep the same database behavior.
    """
    if event is None:
        required = {
            "user_id": user_id,
            "url": url,
            "website": website,
            "media_type": media_type,
            "quality": quality,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError("missing download event fields: " + ", ".join(missing))
        event = DownloadEvent(
            user_id=int(user_id),
            username=username,
            url=str(url),
            website=str(website),
            media_type=str(media_type),
            quality=str(quality),
            created_at=created_at,
        )

    if not isinstance(event, DownloadEvent):
        raise TypeError("event must be a DownloadEvent")
    if not event.ledger_eligible:
        raise ValueError("only successfully delivered events may enter the ledger")

    conn = get_db()
    try:
        with conn:
            conn.execute(
                """INSERT INTO downloads
                   (user_id, username, url, website, media_type, quality, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)"""
                ,(
                    event.user_id,
                    event.username,
                    event.url,
                    event.website,
                    event.media_type,
                    event.quality,
                    event.timestamp,
                ),
            )
            conn.execute(
                "UPDATE users SET downloads = downloads + 1 WHERE user_id = ?",
                (event.user_id,),
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
