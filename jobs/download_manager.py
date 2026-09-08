"""Bounded concurrency and per-user serialization for downloads."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class DownloadBusyError(RuntimeError):
    """Raised when the download manager cannot accept work promptly."""


class DownloadJobManager:
    """Limit expensive media work without creating an unbounded queue."""

    def __init__(self, *, max_concurrent: int = 2, per_user_timeout: float = 2.0) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        if per_user_timeout < 0:
            raise ValueError("per_user_timeout must not be negative")
        self._global = asyncio.Semaphore(max_concurrent)
        self._per_user: dict[int, asyncio.Lock] = {}
        self._map_lock = asyncio.Lock()
        self._per_user_timeout = per_user_timeout

    async def _user_lock(self, user_id: int) -> asyncio.Lock:
        async with self._map_lock:
            lock = self._per_user.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                self._per_user[user_id] = lock
            return lock

    @asynccontextmanager
    async def slot(self, user_id: int):
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        user_lock = await self._user_lock(user_id)
        try:
            await asyncio.wait_for(user_lock.acquire(), self._per_user_timeout)
        except asyncio.TimeoutError as exc:
            raise DownloadBusyError("user already has an active download") from exc
        global_acquired = False
        try:
            try:
                await asyncio.wait_for(self._global.acquire(), self._per_user_timeout)
                global_acquired = True
            except asyncio.TimeoutError as exc:
                raise DownloadBusyError("download service is temporarily busy") from exc
            yield
        finally:
            if global_acquired:
                self._global.release()
            user_lock.release()

    async def run(self, user_id: int, operation):
        async with self.slot(user_id):
            return await operation()

    async def close(self) -> None:
        async with self._map_lock:
            self._per_user.clear()
