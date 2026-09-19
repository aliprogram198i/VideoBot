"""Bounded, fair download admission queue with per-user serialization.

The public slot() API remains compatible with the previous semaphore manager.
Requests now receive FIFO ordering within the same priority and can be cancelled
or timed out without leaking capacity.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class DownloadBusyError(RuntimeError):
    """Raised when a download cannot be admitted within its queue timeout."""


class DownloadJobManager:
    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        per_user_timeout: float = 2.0,
        queue_timeout: float = 2.0,
        max_queue: int | None = None,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        if per_user_timeout < 0 or queue_timeout < 0:
            raise ValueError("timeouts must not be negative")
        self._max_concurrent = max_concurrent
        self._per_user: dict[int, asyncio.Lock] = {}
        self._map_lock = asyncio.Lock()
        self._queue_lock = asyncio.Lock()
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(
            maxsize=max_queue if max_queue is not None else max_concurrent * 4
        )
        self._sequence = 0
        self._active = 0
        self._per_user_timeout = per_user_timeout
        self._queue_timeout = queue_timeout
        self._closed = False

    async def _user_lock(self, user_id: int) -> asyncio.Lock:
        async with self._map_lock:
            lock = self._per_user.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                self._per_user[user_id] = lock
            return lock

    async def _enqueue(self, *, priority: int = 0, timeout: float | None = None) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        async with self._queue_lock:
            if self._closed:
                raise DownloadBusyError("download manager is closed")
            self._sequence += 1
            ticket = (int(priority), self._sequence, future)
            try:
                await asyncio.wait_for(
                    self._queue.put(ticket),
                    self._queue_timeout if timeout is None else timeout,
                )
            except asyncio.TimeoutError as exc:
                raise DownloadBusyError("download queue is full") from exc
            self._pump_locked()
        return future

    def _pump_locked(self) -> None:
        while self._active < self._max_concurrent and not self._queue.empty():
            _, _, future = self._queue.get_nowait()
            if future.cancelled():
                continue
            self._active += 1
            future.set_result(True)

    async def _release(self) -> None:
        async with self._queue_lock:
            self._active = max(0, self._active - 1)
            self._pump_locked()

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return self._active

    @asynccontextmanager
    async def slot(self, user_id: int, *, priority: int = 0, queue_timeout: float | None = None):
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        user_lock = await self._user_lock(user_id)
        try:
            await asyncio.wait_for(user_lock.acquire(), self._per_user_timeout)
        except asyncio.TimeoutError as exc:
            raise DownloadBusyError("user already has an active download") from exc

        global_acquired = False
        future = None
        try:
            future = await self._enqueue(priority=priority, timeout=queue_timeout)
            try:
                await asyncio.wait_for(
                    future,
                    self._queue_timeout if queue_timeout is None else queue_timeout,
                )
            except asyncio.TimeoutError as exc:
                if future.done() and not future.cancelled():
                    global_acquired = True
                else:
                    future.cancel()
                if not global_acquired:
                    raise DownloadBusyError("download queue wait timed out") from exc
            global_acquired = True
            yield
        except asyncio.CancelledError:
            if future is not None:
                future.cancel()
            raise
        finally:
            if global_acquired:
                await self._release()
            user_lock.release()

    async def run(self, user_id: int, operation, *, priority: int = 0):
        async with self.slot(user_id, priority=priority):
            return await operation()

    async def close(self) -> None:
        async with self._queue_lock:
            self._closed = True
            while not self._queue.empty():
                _, _, future = self._queue.get_nowait()
                if not future.done():
                    future.cancel()
            self._active = 0
        async with self._map_lock:
            self._per_user.clear()
