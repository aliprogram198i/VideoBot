"""Composition boundary for rate limiting and download concurrency."""

from __future__ import annotations

from functools import wraps
from typing import Any

from jobs.download_manager import DownloadBusyError, DownloadJobManager
from security.rate_limit import RateLimitExceeded, RateLimiter


class DownloadRejected(RuntimeError):
    """Raised internally when a download is rejected by a safety guard."""


def install_download_guards(bot_module: Any, *, max_concurrent: int = 2, user_rate_capacity: int = 5, user_rate_window_seconds: float = 60.0) -> None:
    """Wrap the legacy download callback with bounded, rate-limited execution."""
    if getattr(bot_module, "DOWNLOAD_GUARDS_INSTALLED", False):
        return
    original = getattr(bot_module, "download_media", None)
    if original is None:
        raise RuntimeError("download_media handler is missing")
    manager = DownloadJobManager(max_concurrent=max_concurrent)
    limiter = RateLimiter(user_capacity=user_rate_capacity, user_refill_seconds=user_rate_window_seconds)

    @wraps(original)
    async def guarded_download_media(update, context):
        user = getattr(update, "effective_user", None)
        user_id = getattr(user, "id", None)
        query = getattr(update, "callback_query", None)
        if not isinstance(user_id, int) or user_id <= 0:
            raise DownloadRejected("missing Telegram user identity")
        try:
            limiter.check(user_id)
            async with manager.slot(user_id):
                return await original(update, context)
        except RateLimitExceeded:
            if query is not None:
                try:
                    await query.answer("⏳ طلبات كثيرة. حاول بعد قليل.", show_alert=True)
                except Exception:
                    pass
            return None
        except DownloadBusyError:
            if query is not None:
                try:
                    await query.answer("⏳ لديك تحميل جارٍ أو الخدمة مشغولة. حاول بعد لحظات.", show_alert=True)
                except Exception:
                    pass
            return None

    bot_module.download_media = guarded_download_media
    bot_module.DOWNLOAD_JOB_MANAGER = manager
    bot_module.DOWNLOAD_RATE_LIMITER = limiter
    bot_module.DOWNLOAD_GUARDS_INSTALLED = True
    print("🛡️ Download guards: enabled (global=2, per-user=1, rate=5/min)", flush=True)
