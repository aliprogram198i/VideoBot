"""Canonical download event contract shared by delivery, ledger, and telemetry.

The event represents the outcome of one user download attempt. The ledger only
accepts successful delivery events; telemetry may record both delivered and
terminally failed outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


MEDIA_TYPES = frozenset({"video", "audio", "image"})
DELIVERY_STATUSES = frozenset({"delivered", "failed"})


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
    elapsed_ms: float | None = None
    failure_reason: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if int(self.user_id) <= 0:
            raise ValueError("user_id must be positive")
        if not str(self.url).strip():
            raise ValueError("url must be non-empty")
        if self.media_type not in MEDIA_TYPES:
            raise ValueError(f"unsupported media_type: {self.media_type}")
        if self.delivery_status not in DELIVERY_STATUSES:
            raise ValueError(f"unsupported delivery_status: {self.delivery_status}")
        if self.attempt_number is not None and int(self.attempt_number) <= 0:
            raise ValueError("attempt_number must be positive")
        if int(self.delivered_parts) < 0:
            raise ValueError("delivered_parts must be non-negative")
        if self.delivery_status == "delivered" and int(self.delivered_parts) <= 0:
            raise ValueError("delivered_parts must be positive for delivered events")
        if self.delivery_status == "failed" and self.delivered_parts != 0:
            raise ValueError("failed events must have delivered_parts=0")
        if self.elapsed_ms is not None and float(self.elapsed_ms) < 0:
            raise ValueError("elapsed_ms must be non-negative")
        if self.delivery_status == "failed" and not str(self.failure_reason or "").strip():
            raise ValueError("failure_reason is required for failed events")

    @property
    def timestamp(self) -> str:
        return self.created_at or datetime.now(timezone.utc).isoformat()

    @property
    def success(self) -> bool:
        return self.delivery_status == "delivered"

    @property
    def ledger_eligible(self) -> bool:
        return self.success

    @classmethod
    def failed(
        cls,
        *,
        user_id: int,
        url: str,
        website: str,
        media_type: str,
        quality: str,
        failure_reason: str,
        username: str | None = None,
        attempt_id: str | None = None,
        attempt_number: int | None = None,
        elapsed_ms: float | None = None,
        created_at: str | None = None,
    ) -> "DownloadEvent":
        return cls(
            user_id=user_id,
            username=username,
            url=url,
            website=website,
            media_type=media_type,
            quality=quality,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            delivery_status="failed",
            delivered_parts=0,
            elapsed_ms=elapsed_ms,
            failure_reason=failure_reason,
            created_at=created_at,
        )
