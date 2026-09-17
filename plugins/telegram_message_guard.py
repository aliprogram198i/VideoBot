"""Safe Telegram callback-message edit guard.

Protect callback-driven user/admin flows from stale or deleted Telegram
messages. A stale callback edit is ignored rather than creating a new message,
which prevents an obsolete audio/video menu from reappearing after delivery.
Normal ``Message.edit_text`` calls are intentionally left untouched because
they are used by live progress/status messages and must retain native
Telegram behavior.
"""

from __future__ import annotations

import functools
import logging

from telegram import CallbackQuery
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

_INSTALLED = False


def _edit_failure_kind(exc: BadRequest) -> str | None:
    """Classify callback edit failures that represent stale UI state."""
    message = str(exc).strip().lower()
    if "message to edit not found" in message or "message to edit is not found" in message:
        return "not_found"
    if "message is not modified" in message:
        return "not_modified"
    if "message can't be edited" in message or "message cannot be edited" in message:
        return "not_editable"
    return None


def _wrap_callback_edit(original):
    @functools.wraps(original)
    async def safe_edit(self: CallbackQuery, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except BadRequest as exc:
            kind = _edit_failure_kind(exc)
            if kind is None:
                raise
            logger.info(
                "Ignored Telegram callback-message edit failure: %s",
                kind,
            )
            return None
    return safe_edit


def install() -> None:
    """Install the callback edit guard exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return

    CallbackQuery.edit_message_text = _wrap_callback_edit(CallbackQuery.edit_message_text)
    _INSTALLED = True
    logger.info("Telegram message edit guard: ENABLED (callback only)")


install()

__all__ = ["install"]
