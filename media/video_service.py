"""Dedicated video-download policy.

This module contains only video-specific format selection. Transport/provider
execution remains outside the service so the bot can migrate incrementally
without duplicating network/security code.
"""

from __future__ import annotations


VIDEO_FORMATS = {
    "video_best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    "video_1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "video_720": "bestvideo*[height<=720]+bestaudio/best[height<=720]/best",
    "video_480": "bestvideo*[height<=480]+bestaudio/best[height<=480]/best",
    "video_360": "bestvideo*[height<=360]+bestaudio/best[height<=360]/best",
}

VIDEO_QUALITY_NAMES = {
    "video_best": "Maximum available quality",
    "video_1080": "1080p",
    "video_720": "720p",
    "video_480": "480p",
    "video_360": "360p",
}


class VideoService:
    """Own deterministic video-quality policy without network side effects."""

    def format_selector(self, choice: str) -> str:
        try:
            return VIDEO_FORMATS[choice]
        except KeyError as exc:
            raise ValueError(f"Unsupported video quality: {choice}") from exc

    def quality_name(self, choice: str) -> str:
        try:
            return VIDEO_QUALITY_NAMES[choice]
        except KeyError as exc:
            raise ValueError(f"Unsupported video quality: {choice}") from exc
