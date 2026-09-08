"""In-process rate limiting for expensive bot operations."""

from __future__ import annotations

import time
from dataclasses import dataclass


class RateLimitExceeded(RuntimeError):
    """Raised when a caller exceeds an operation budget."""


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """Thread/event-loop safe enough for a single PTB process."""

    def __init__(self, *, user_capacity: int = 5, user_refill_seconds: float = 60.0, global_capacity: int = 30, global_refill_seconds: float = 60.0) -> None:
        if user_capacity < 1 or global_capacity < 1:
            raise ValueError("bucket capacities must be positive")
        if user_refill_seconds <= 0 or global_refill_seconds <= 0:
            raise ValueError("refill intervals must be positive")
        self.user_capacity = float(user_capacity)
        self.user_refill_rate = self.user_capacity / user_refill_seconds
        self.global_capacity = float(global_capacity)
        self.global_refill_rate = self.global_capacity / global_refill_seconds
        self._users: dict[int, _Bucket] = {}
        self._global = _Bucket(self.global_capacity, time.monotonic())

    @staticmethod
    def _refill(bucket: _Bucket, capacity: float, rate: float, now: float) -> None:
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(capacity, bucket.tokens + elapsed * rate)
        bucket.updated_at = now

    def allow(self, user_id: int, *, cost: float = 1.0, now: float | None = None) -> bool:
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        if cost <= 0:
            raise ValueError("cost must be positive")
        current = time.monotonic() if now is None else now
        bucket = self._users.get(user_id)
        if bucket is None:
            bucket = _Bucket(self.user_capacity, current)
            self._users[user_id] = bucket
        self._refill(bucket, self.user_capacity, self.user_refill_rate, current)
        self._refill(self._global, self.global_capacity, self.global_refill_rate, current)
        if bucket.tokens < cost or self._global.tokens < cost:
            return False
        bucket.tokens -= cost
        self._global.tokens -= cost
        return True

    def check(self, user_id: int, *, cost: float = 1.0, now: float | None = None) -> None:
        if not self.allow(user_id, cost=cost, now=now):
            raise RateLimitExceeded("download rate limit exceeded")

    def retry_after(self, user_id: int, *, cost: float = 1.0, now: float | None = None) -> float:
        if user_id <= 0 or cost <= 0:
            raise ValueError("invalid rate-limit arguments")
        current = time.monotonic() if now is None else now
        bucket = self._users.get(user_id)
        if bucket is None:
            return 0.0
        self._refill(bucket, self.user_capacity, self.user_refill_rate, current)
        missing = max(0.0, cost - bucket.tokens)
        return missing / self.user_refill_rate if self.user_refill_rate else 0.0

    def clear(self) -> None:
        self._users.clear()
        self._global = _Bucket(self.global_capacity, time.monotonic())
