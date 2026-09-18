"""Canonical Download -> Delivery -> Ledger event contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DownloadEvent:
    user_id: int
    url: str
    website: str
    media_type: str
    quality: str
    username: str | None = None
    attempt_id: str | None = None
    attempt_number: int | None = None
    delivery_status: str = "delivered"
    delivered_parts: int = 1
    created_at: str | None = None

    def __post_init__(self) -> None:
        if int(self.user_id) <= 0:
            raise ValueError("user_id must be positive")
        if not str(self.url).strip():
            raise ValueError("url must be non-empty")
        if self.media_type not in {"video", "audio"}:
            raise ValueError("media_type must be video or audio")
        if self.delivery_status not in {"delivered"}:
            raise ValueError("ledger events must represent successful delivery")
        if int(self.delivered_parts) <= 0:
            raise ValueError("delivered_parts must be positive")

    @property
    def timestamp(self) -> str:
        return self.created_at or datetime.now(timezone.utc).isoformat()
