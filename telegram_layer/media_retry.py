"""Targeted Telegram media delivery retry layer.

Retries only Telegram flood-control responses for video uploads. Other
exceptions and all non-video Telegram operations keep their existing behavior.
"""

from __future__ import annotations

import asyncio
import functools
import logging

from telegram import Bot
from telegram.error import RetryAfter

logger = logging.getLogger(__name__)

MAX_RETRY_AFTER_ATTEMPTS = 6
MAX_RETRY_AFTER_SECONDS = 120


def install_telegram_media_retry() -> None:
    """Install an idempotent RetryAfter handler around Bot.send_video."""
    if getattr(Bot, "_alibot_media_retry_installed", False):
        return

    original_send_video = Bot.send_video

    @functools.wraps(original_send_video)
    async def send_video_with_retry(self, *args, **kwargs):
        for attempt in range(MAX_RETRY_AFTER_ATTEMPTS + 1):
            try:
                return await original_send_video(self, *args, **kwargs)
            except RetryAfter as exc:
                if attempt >= MAX_RETRY_AFTER_ATTEMPTS:
                    logger.error(
                        "Telegram media delivery: retry limit reached after RetryAfter=%ss",
                        exc.retry_after,
                    )
                    raise

                retry_after = max(1.0, float(exc.retry_after))
                delay = min(retry_after, MAX_RETRY_AFTER_SECONDS)
                logger.warning(
                    "Telegram media delivery: RetryAfter=%ss; waiting %.1fs before retry %d/%d",
                    exc.retry_after,
                    delay,
                    attempt + 1,
                    MAX_RETRY_AFTER_ATTEMPTS,
                )
                await asyncio.sleep(delay)

    Bot.send_video = send_video_with_retry
    Bot._alibot_media_retry_installed = True
    logger.info("🛡️ Telegram media retry layer: ENABLED (RetryAfter-aware video delivery)")
