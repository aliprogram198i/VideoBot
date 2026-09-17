"""Safe Telegram message-edit guard.

Protect user/admin flows from stale or deleted Telegram messages. A stale
callback edit is treated as an obsolete UI action and is ignored; it must not
create a new message because that can place an old menu after a successfully
delivered media file. Message.edit_text keeps the existing safe reply fallback
for non-callback flows.
"""

from __future__ import annotations

import functools
import logging

from telegram import CallbackQuery, Message
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

_INSTALLED = False


def _edit_failure_kind(exc: BadRequest) -> str | None:
    """Classify Telegram edit failures that must never resurrect old UI."""
    message = str(exc).strip().lower()
    if "message to edit not found" in message or "message to edit is not found" in message:
        return "not_found"
    if "message is not modified" in message:
        return "not_modified"
    if "message can't be edited" in message or "message cannot be edited" in message:
        return "not_editable"
    return None


def _fallback_reply(message, text, kwargs):
    if message is None or text is None:
        logger.warning("Telegram stale-message edit has no fallback target")
        return None
    fallback_kwargs = dict(kwargs)
    fallback_kwargs.pop("text", None)
    try:
        return await message.reply_text(text, **fallback_kwargs)
    except Exception:
        logger.exception("Telegram stale-message fallback failed")
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
            # A callback edit failure means the callback UI is stale, already
            # applied, or no longer editable. Never reply with the requested
            # menu text: doing so can resurrect an old audio/video menu after
            # the media file has already been delivered.
            logger.info(
                "Ignored Telegram callback-message edit failure: %s",
                kind,
            )
            return None
    return safe_edit


def _wrap_message_edit(original):
    @functools.wraps(original)
    async def safe_edit(self: Message, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except BadRequest as exc:
            kind = _edit_failure_kind(exc)
            if kind is None:
                raise
            if kind in {"not_modified", "not_editable"}:
                logger.info(
                    "Ignored Telegram message edit failure: %s",
                    kind,
                )
                return None
            text = args[0] if args else kwargs.get("text")
            return await _fallback_reply(self, text, kwargs)
    return safe_edit


def install() -> None:
    """Install both Telegram edit guards exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return

    CallbackQuery.edit_message_text = _wrap_callback_edit(CallbackQuery.edit_message_text)
    Message.edit_text = _wrap_message_edit(Message.edit_text)
    _INSTALLED = True
    logger.info("Telegram message edit guard: ENABLED (callback + message)")


install()

__all__ = ["install"]
