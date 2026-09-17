"""Safe Telegram callback-message editing guard.

This module protects user/admin callback flows from a stale or deleted
Telegram message. It only intercepts the specific BadRequest raised when
Telegram reports that the target message no longer exists. All other Telegram
errors are re-raised unchanged.
"""

from __future__ import annotations

import logging

from telegram import CallbackQuery
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

_INSTALLED = False
_ORIGINAL_EDIT_MESSAGE_TEXT = None


async def _safe_edit_message_text(self: CallbackQuery, *args, **kwargs):
    """Edit a callback message, falling back to a new message when stale."""
    try:
        return await _ORIGINAL_EDIT_MESSAGE_TEXT(self, *args, **kwargs)
    except BadRequest as exc:
        if "Message to edit not found" not in str(exc):
            raise

        message = getattr(self, "message", None)
        text = args[0] if args else kwargs.get("text")
        if message is None or text is None:
            logger.warning(
                "Telegram callback message is stale and has no fallback target"
            )
            return None

        fallback_kwargs = dict(kwargs)
        fallback_kwargs.pop("text", None)
        try:
            return await message.reply_text(text, **fallback_kwargs)
        except Exception:
            logger.exception(
                "Telegram stale-message fallback failed"
            )
            return None


def install() -> None:
    """Install the guard once during application startup."""
    global _INSTALLED, _ORIGINAL_EDIT_MESSAGE_TEXT
    if _INSTALLED:
        return
    _ORIGINAL_EDIT_MESSAGE_TEXT = CallbackQuery.edit_message_text
    CallbackQuery.edit_message_text = _safe_edit_message_text
    _INSTALLED = True
    logger.info("Telegram message edit guard: ENABLED")


install()

__all__ = ["install"]
