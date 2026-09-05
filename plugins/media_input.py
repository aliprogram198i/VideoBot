"""AliBot Telegram Media Input layer.

Receives media shared directly to the bot (including media forwarded from
WhatsApp), stores it only in a per-request temporary directory, and returns
it to the user without touching the existing URL downloader pipeline or DB.
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters


MAX_MEDIA_BYTES = 500 * 1024 * 1024
MAX_CONCURRENT_MEDIA_JOBS = 2

_MEDIA_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_MEDIA_JOBS)

_VIDEO_MIMES = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/x-matroska",
    "video/x-msvideo",
    "video/mpeg",
}

_AUDIO_MIMES = {
    "audio/mpeg",
    "audio/mp4",
    "audio/aac",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
    "audio/webm",
}


def _safe_name(value, fallback):
    name = Path(value or fallback).name
    name = "".join(ch for ch in name if ch.isalnum() or ch in "._- ()[]")
    return name[:180] or fallback


def _message_media(message):
    if message.video:
        return message.video, "video", message.video.file_size
    if message.voice:
        return message.voice, "voice", message.voice.file_size
    if message.audio:
        return message.audio, "audio", message.audio.file_size
    if message.document:
        mime = (message.document.mime_type or "").lower()
        if mime.startswith("video/") or mime in _VIDEO_MIMES:
            return message.document, "document_video", message.document.file_size
        if mime.startswith("audio/") or mime in _AUDIO_MIMES:
            return message.document, "document_audio", message.document.file_size
    return None, None, None


async def handle_media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direct Telegram media independently from URL messages."""
    message = update.effective_message
    if not message:
        return

    media, media_type, declared_size = _message_media(message)
    if media is None:
        return

    if declared_size and declared_size > MAX_MEDIA_BYTES:
        await message.reply_text(
            "❌ الملف أكبر من الحد المسموح به (500 MB).\n\n"
            "أرسل ملفًا أصغر وحاول مرة أخرى."
        )
        return

    async with _MEDIA_SEMAPHORE:
        temp_dir = tempfile.mkdtemp(prefix="alibot_media_")
        try:
            original_name = getattr(media, "file_name", None)
            if media_type == "video":
                original_name = original_name or "video.mp4"
            elif media_type == "voice":
                original_name = original_name or "voice.ogg"
            elif media_type == "audio":
                original_name = original_name or "audio"
            else:
                original_name = original_name or "media"

            filename = _safe_name(original_name, "media")
            target = Path(temp_dir) / filename

            await message.reply_text("⏳ استلمت الملف، جاري تجهيزه وإرساله...")

            telegram_file = await media.get_file()
            await telegram_file.download_to_drive(custom_path=str(target))

            actual_size = target.stat().st_size
            if actual_size > MAX_MEDIA_BYTES:
                raise ValueError("Downloaded media exceeds configured size limit")
            if actual_size <= 0:
                raise ValueError("Downloaded media is empty")

            if media_type == "video":
                try:
                    await message.reply_video(
                        video=target.open("rb"),
                        caption="🎬 تم استلام الفيديو بنجاح.",
                        supports_streaming=True,
                    )
                except Exception:
                    await message.reply_document(
                        document=target.open("rb"),
                        caption="🎬 تم استلام الفيديو وإرساله كملف.",
                    )
            elif media_type == "voice":
                await message.reply_voice(
                    voice=target.open("rb"),
                    caption="🎙️ تم استلام التسجيل الصوتي بنجاح.",
                )
            elif media_type == "audio":
                try:
                    await message.reply_audio(
                        audio=target.open("rb"),
                        caption="🎵 تم استلام الملف الصوتي بنجاح.",
                        title=Path(filename).stem[:64],
                    )
                except Exception:
                    await message.reply_document(
                        document=target.open("rb"),
                        caption="🎵 تم استلام الملف الصوتي وإرساله كملف.",
                    )
            elif media_type == "document_video":
                await message.reply_document(
                    document=target.open("rb"),
                    caption="🎬 تم استلام الفيديو كملف بنجاح.",
                )
            elif media_type == "document_audio":
                await message.reply_document(
                    document=target.open("rb"),
                    caption="🎵 تم استلام الملف الصوتي بنجاح.",
                )

        except Exception:
            await message.reply_text(
                "❌ تعذر معالجة الملف حاليًا.\n\n"
                "تأكد أن الملف صالح وحجمه لا يتجاوز 500 MB، ثم حاول مرة أخرى."
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def register_media_input(app):
    """Register media handlers without changing the existing text handlers."""
    media_filter = filters.VIDEO | filters.VOICE | filters.AUDIO | filters.Document.ALL
    app.add_handler(MessageHandler(media_filter, handle_media_input), group=0)
