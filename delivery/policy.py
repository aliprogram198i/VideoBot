"""Centralized media artifact and Telegram delivery constraints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeliveryPolicy:
    """Single source of truth for artifact and Telegram upload limits."""

    max_video_bytes: int = 500 * 1024 * 1024
    max_audio_bytes: int = 500 * 1024 * 1024
    max_image_bytes: int = 50 * 1024 * 1024
    max_telegram_video_bytes: int = 49 * 1024 * 1024
    max_telegram_audio_bytes: int = 47 * 1024 * 1024
    max_telegram_image_bytes: int = 50 * 1024 * 1024

    def validate_file(self, path: str | Path, *, media_type: str) -> Path:
        """Validate a complete local artifact before delivery preparation."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(file_path)

        size = file_path.stat().st_size
        if size <= 0:
            raise ValueError("media file is empty")

        media_kind = str(media_type).lower()
        if media_kind == "audio":
            limit = self.max_audio_bytes
        elif media_kind == "image":
            limit = self.max_image_bytes
        else:
            limit = self.max_video_bytes
        if size > limit:
            raise ValueError(f"media exceeds artifact limit: {size} > {limit}")
        return file_path

    def validate_telegram_upload(
        self,
        path: str | Path,
        *,
        media_type: str,
    ) -> Path:
        """Mandatory final gate immediately before each Telegram upload."""
        file_path = self.validate_file(path, media_type=media_type)
        size = file_path.stat().st_size
        media_kind = str(media_type).lower()

        if media_kind == "audio":
            limit = self.max_telegram_audio_bytes
        elif media_kind == "image":
            limit = self.max_telegram_image_bytes
        else:
            limit = self.max_telegram_video_bytes
        if size > limit:
            raise ValueError(
                f"media exceeds Telegram upload limit: {size} > {limit}"
            )
        return file_path
