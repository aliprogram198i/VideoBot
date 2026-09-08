"""Add durable content titles to successful download history.

The existing download flow calls bot_module.save_download after delivery. This
module keeps that call site stable while taking ownership of the database write,
adding an idempotent title column, and resolving a real yt-dlp title when
available. Title lookup is best-effort and never prevents a successful download
from being recorded.
"""

from __future__ import annotations

import subprocess
from typing import Any


_TITLE_TIMEOUT_SECONDS = 12
_MAX_TITLE_LENGTH = 500


def _ensure_title_column(get_db) -> None:
    conn = get_db()
    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(downloads)").fetchall()
        }
        if "title" not in columns:
            conn.execute("ALTER TABLE downloads ADD COLUMN title TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_downloads_created_at "
            "ON downloads(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_downloads_user_id "
            "ON downloads(user_id)"
        )
        conn.commit()
    finally:
        conn.close()


def _extract_title(bot_module: Any, url: str) -> str | None:
    """Resolve a real extractor title without invoking a shell."""
    try:
        bot_module.validate_public_http_url(url)
        command = [
            "python",
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--skip-download",
            "--no-warnings",
            "--print",
            "title",
            "--extractor-args",
            "youtube:player_client=android,web",
            url,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_TITLE_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            title = line.strip()
            if title:
                return title[:_MAX_TITLE_LENGTH]
    except Exception:
        return None
    return None


def register_download_log_enrichment(bot_module: Any) -> None:
    """Migrate the table and replace save_download with the title-aware writer."""
    get_db = bot_module.get_db
    _ensure_title_column(get_db)

    if getattr(bot_module, "_download_log_enrichment_registered", False):
        return

    def save_download_with_title(user, url, website, media_type, quality):
        title = _extract_title(bot_module, url)
        conn = get_db()
        try:
            conn.execute(
                """
                INSERT INTO downloads (
                    user_id,
                    username,
                    url,
                    website,
                    media_type,
                    quality,
                    title,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.username,
                    url,
                    website,
                    media_type,
                    quality,
                    title,
                    bot_module.datetime.now().isoformat(),
                ),
            )
            conn.execute(
                """
                UPDATE users
                SET downloads = downloads + 1
                WHERE user_id = ?
                """,
                (user.id,),
            )
            conn.commit()
        finally:
            conn.close()

    bot_module.save_download = save_download_with_title
    bot_module._download_log_enrichment_registered = True
