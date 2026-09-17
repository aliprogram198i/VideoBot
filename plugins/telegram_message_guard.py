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


def _is_stale_message_error(exc: BadRequest) -> bool:
    return "Message to edit not found" in str(exc)


async def _fallback_reply(message, text, kwargs):
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
            if not _is_stale_message_error(exc):
                raise
            # The callback points at an obsolete Telegram message. Do not
            # reply with the requested UI text: doing so resurrects stale menus
            # after a successful media delivery and can produce out-of-order
            # prompts such as the audio-quality menu appearing below a file.
            logger.info("Ignored stale Telegram callback-message edit")
            return None
    return safe_edit


def _wrap_message_edit(original):
    @functools.wraps(original)
    async def safe_edit(self: Message, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except BadRequest as exc:
            if not _is_stale_message_error(exc):
                raise
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
