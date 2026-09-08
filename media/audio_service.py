"""Dedicated audio-download policy.

Audio quality is intentionally represented separately from video quality.
The provider/transport layer remains responsible for downloading; this
service only owns the audio format and FFmpeg-quality policy.
"""

from __future__ import annotations

AUDIO_FORMAT = "bestaudio/best[acodec!=none]"

AUDIO_QUALITIES = {
    "audio_best": ("Best audio", "0"),
    "audio_320": ("320 kbps", "320K"),
    "audio_256": ("256 kbps", "256K"),
    "audio_192": ("192 kbps", "192K"),
    "audio_128": ("128 kbps", "128K"),
}


class AudioService:
    """Own deterministic audio-quality policy without network side effects."""

    def format_selector(self, choice: str) -> str:
        if choice not in AUDIO_QUALITIES:
            raise ValueError(f"Unsupported audio quality: {choice}")
        return AUDIO_FORMAT

    def quality_name(self, choice: str) -> str:
        try:
            return AUDIO_QUALITIES[choice][0]
        except KeyError as exc:
            raise ValueError(f"Unsupported audio quality: {choice}") from exc

    def audio_quality(self, choice: str) -> str:
        try:
            return AUDIO_QUALITIES[choice][1]
        except KeyError as exc:
            raise ValueError(f"Unsupported audio quality: {choice}") from exc
