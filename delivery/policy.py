"""Centralized Telegram media delivery constraints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeliveryPolicy:
    max_video_bytes: int = 500 * 1024 * 1024
    max_audio_bytes: int = 500 * 1024 * 1024
    max_telegram_audio_bytes: int = 47 * 1024 * 1024

    def validate_file(self, path: str | Path, *, media_type: str) -> Path:
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(file_path)
        size = file_path.stat().st_size
        limit = self.max_audio_bytes if media_type == "audio" else self.max_video_bytes
        if media_type == "audio":
            limit = min(limit, self.max_telegram_audio_bytes)
        if size > limit:
            raise ValueError(f"media exceeds delivery limit: {size} > {limit}")
        return file_path
