"""Observable download lifecycle with correlation IDs.

This layer is side-effect free with respect to the database: it emits compact
structured lifecycle events to the application log and keeps correlation state
in memory/context. Existing download ledger writes remain unchanged.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator


class DownloadState(str, Enum):
    REQUESTED = "requested"
    VALIDATING = "validating"
    RESOLVING = "resolving"
    CANDIDATE_FOUND = "candidate_found"
    DOWNLOADING = "downloading"
    POSTPROCESSING = "postprocessing"
    DELIVERING = "delivering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadLifecycle:
    user_id: int
    source_url: str
    platform: str
    media_type: str
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: DownloadState = DownloadState.REQUESTED
    started_at: float = field(default_factory=time.monotonic)

    def emit(self, state: DownloadState, **fields: Any) -> None:
        self.state = state
        payload = {
            "event": "download_lifecycle",
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "user_id": self.user_id,
            "platform": self.platform,
            "media_type": self.media_type,
            "state": state.value,
            "elapsed_ms": int((time.monotonic() - self.started_at) * 1000),
            **fields,
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)

    def transition(self, state: DownloadState, **fields: Any) -> None:
        self.emit(state, **fields)


@asynccontextmanager
async def lifecycle_context(
    *,
    user_id: int,
    source_url: str,
    platform: str,
    media_type: str,
    job_id: str | None = None,
    attempt_id: str | None = None,
) -> AsyncIterator[DownloadLifecycle]:
    lifecycle = DownloadLifecycle(
        user_id=user_id,
        source_url=source_url,
        platform=platform,
        media_type=media_type,
        job_id=job_id or uuid.uuid4().hex,
        attempt_id=attempt_id or uuid.uuid4().hex,
    )
    lifecycle.emit(DownloadState.REQUESTED)
    try:
        yield lifecycle
    except BaseException as exc:
        if type(exc).__name__ == "CancelledError":
            lifecycle.emit(DownloadState.CANCELLED, error_type=type(exc).__name__)
        else:
            lifecycle.emit(DownloadState.FAILED, error_type=type(exc).__name__)
        raise
