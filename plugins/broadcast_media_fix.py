from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _BotProxy:
    """Delegate Telegram Bot operations while adding a safe media fallback."""

    def __init__(self, bot: Any, payload: dict[str, Any]) -> None:
        self._bot = bot
        self._payload = payload

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bot, name)

    async def copy_message(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await self._bot.copy_message(*args, **kwargs)
        except Exception as exc:
            from telegram.error import RetryAfter

            if isinstance(exc, RetryAfter):
                raise

            kind = self._payload.get("kind")
            file_id = self._payload.get("file_id")
            if not file_id or kind not in {"photo", "video", "audio", "voice", "document"}:
                raise

            logger.warning(
                "Rich broadcast copy_message failed; using file_id fallback: kind=%s error=%s",
                kind,
                type(exc).__name__,
            )
            chat_id = kwargs.get("chat_id")
            if chat_id is None and args:
                chat_id = args[0]
            caption = self._payload.get("caption")
            if kind == "photo":
                return await self._bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
            if kind == "video":
                return await self._bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
            if kind == "audio":
                return await self._bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
            if kind == "voice":
                return await self._bot.send_voice(chat_id=chat_id, voice=file_id, caption=caption)
            return await self._bot.send_document(chat_id=chat_id, document=file_id, caption=caption)


class _ContextProxy:
    """Expose the original callback context with a proxied bot."""

    def __init__(self, context: Any, bot: Any, payload: dict[str, Any]) -> None:
        self._context = context
        self.bot = _BotProxy(bot, payload)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


def install() -> None:
    from . import alibot_enhancements as target

    if getattr(target, "_alibot_broadcast_media_fix_installed", False):
        return

    original_extract = target._extract_payload
    original_confirm = target._broadcast_confirm

    def extract_with_file_id(message: Any):
        payload = original_extract(message)
        if not payload or payload.get("kind") == "text":
            return payload

        media = None
        kind = payload.get("kind")
        if kind == "photo" and message.photo:
            media = message.photo[-1]
        elif kind == "video" and message.video:
            media = message.video
        elif kind == "audio" and message.audio:
            media = message.audio
        elif kind == "voice" and message.voice:
            media = message.voice
        elif kind == "document" and message.document:
            media = message.document

        if media is not None and getattr(media, "file_id", None):
            payload = dict(payload)
            payload["file_id"] = media.file_id
            payload["caption"] = getattr(message, "caption", None) or None
        return payload

    target._extract_payload = extract_with_file_id

    async def confirm_with_media_fallback(update, context):
        payload = context.user_data.get("rich_broadcast_payload") or {}
        proxied_context = _ContextProxy(context, context.bot, payload)
        return await original_confirm(update, proxied_context)

    target._broadcast_confirm = confirm_with_media_fallback
    target._alibot_broadcast_media_fix_installed = True
    logger.info("Broadcast media fallback installed")
