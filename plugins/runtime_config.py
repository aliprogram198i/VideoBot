"""Centralized runtime configuration for AliBot."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ADMIN_ID = 1486412391
LOCAL_DB_FILE = "bot_stats.db"
VOLUME_DIR = Path("/app/data")
DOWNLOAD_TIMEOUT = 900
YOINKU_DOWNLOAD_TIMEOUT = 300
PROCESS_SHUTDOWN_TIMEOUT = 10
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_VIDEO_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_AUDIO_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_TELEGRAM_AUDIO_MB = 47
MAX_TELEGRAM_AUDIO_BYTES = MAX_TELEGRAM_AUDIO_MB * 1024 * 1024
MAX_YOINKU_RESPONSE_BYTES = 1 * 1024 * 1024
MIN_FREE_SPACE_BYTES = 256 * 1024 * 1024
MAX_BROADCAST_LENGTH = 4000


def get_bot_token() -> str | None:
    return os.getenv("BOT_TOKEN")


def get_gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY")


def get_admin_id() -> int:
    raw = os.getenv("ADMIN_ID")
    if raw is None or not raw.strip():
        return DEFAULT_ADMIN_ID
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("ADMIN_ID must be an integer") from exc
    if value <= 0:
        raise ValueError("ADMIN_ID must be positive")
    return value


def get_database_file() -> str:
    if VOLUME_DIR.is_dir():
        return str(VOLUME_DIR / "bot_stats.db")
    return LOCAL_DB_FILE


def apply_to_bot_module(bot_module) -> None:
    bot_module.TOKEN = get_bot_token()
    bot_module.ADMIN_ID = get_admin_id()
    bot_module.DB_FILE = get_database_file()
    bot_module.GEMINI_API_KEY = get_gemini_api_key()
    for name in (
        "DOWNLOAD_TIMEOUT", "YOINKU_DOWNLOAD_TIMEOUT", "PROCESS_SHUTDOWN_TIMEOUT",
        "MAX_HTML_BYTES", "MAX_VIDEO_DOWNLOAD_BYTES", "MAX_AUDIO_DOWNLOAD_BYTES",
        "MAX_TELEGRAM_AUDIO_MB", "MAX_TELEGRAM_AUDIO_BYTES", "MAX_YOINKU_RESPONSE_BYTES",
        "MIN_FREE_SPACE_BYTES", "MAX_BROADCAST_LENGTH",
    ):
        setattr(bot_module, name, globals()[name])
