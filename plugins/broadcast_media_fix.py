from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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
        original_copy = context.bot.copy_message
        payload = context.user_data.get("rich_broadcast_payload") or {}

        async def resilient_copy_message(*args, **kwargs):
            try:
                return await original_copy(*args, **kwargs)
            except Exception as exc:
                from telegram.error import RetryAfter
                if isinstance(exc, RetryAfter):
                    raise

                kind = payload.get("kind")
                file_id = payload.get("file_id")
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
                caption = payload.get("caption")
                if kind == "photo":
                    return await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
                if kind == "video":
                    return await context.bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
                if kind == "audio":
                    return await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
                if kind == "voice":
                    return await context.bot.send_voice(chat_id=chat_id, voice=file_id, caption=caption)
                return await context.bot.send_document(chat_id=chat_id, document=file_id, caption=caption)

        context.bot.copy_message = resilient_copy_message
        try:
            return await original_confirm(update, context)
        finally:
            context.bot.copy_message = original_copy

    target._broadcast_confirm = confirm_with_media_fallback
    target._alibot_broadcast_media_fix_installed = True
    logger.info("Broadcast media fallback installed")
