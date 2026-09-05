"""AliBot Telegram Media Input layer."""

import asyncio
import shutil
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

MAX_MEDIA_BYTES = 500 * 1024 * 1024
MAX_CONCURRENT_MEDIA_JOBS = 2
_MEDIA_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_MEDIA_JOBS)


def _safe_name(value, fallback="media"):
    name = Path(value or fallback).name
    name = "".join(ch for ch in name if ch.isalnum() or ch in "._- ()[]")
    return name[:180] or fallback


def _message_media(message):
    if message.video:
        return message.video, "video"
    if message.voice:
        return message.voice, "voice"
    if message.audio:
        return message.audio, "audio"
    if message.document:
        mime = (message.document.mime_type or "").lower()
        if mime.startswith("video/"):
            return message.document, "document_video"
        if mime.startswith("audio/"):
            return message.document, "document_audio"
    return None, None


def _declared_size(media):
    try:
        return int(getattr(media, "file_size", 0) or 0)
    except (TypeError, ValueError):
        return 0


async def handle_media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return

    media, media_type = _message_media(message)
    if media is None:
        return

    if _declared_size(media) > MAX_MEDIA_BYTES:
        await message.reply_text("❌ الملف أكبر من الحد المسموح به (500 MB).\n\nأرسل ملفًا أصغر وحاول مرة أخرى.")
        return

    async with _MEDIA_SEMAPHORE:
        temp_dir = tempfile.mkdtemp(prefix="alibot_media_")
        try:
            defaults = {
                "video": "video.mp4",
                "voice": "voice.ogg",
                "audio": "audio",
                "document_video": "video",
                "document_audio": "audio",
            }
            target = Path(temp_dir) / _safe_name(getattr(media, "file_name", None), defaults[media_type])
            await message.reply_text("⏳ استلمت الملف، جاري تجهيزه وإرساله...")
            telegram_file = await media.get_file()
            await telegram_file.download_to_drive(custom_path=str(target))
            actual_size = target.stat().st_size
            if actual_size <= 0 or actual_size > MAX_MEDIA_BYTES:
                raise ValueError("Invalid downloaded media size")

            if media_type == "video":
                try:
                    with target.open("rb") as fh:
                        await message.reply_video(video=fh, caption="🎬 تم استلام الفيديو بنجاح.", supports_streaming=True)
                except Exception:
                    with target.open("rb") as fh:
                        await message.reply_document(document=fh, caption="🎬 تم استلام الفيديو وإرساله كملف.")
            elif media_type == "voice":
                with target.open("rb") as fh:
                    await message.reply_voice(voice=fh, caption="🎙️ تم استلام التسجيل الصوتي بنجاح.")
            elif media_type == "audio":
                try:
                    with target.open("rb") as fh:
                        await message.reply_audio(audio=fh, caption="🎵 تم استلام الملف الصوتي بنجاح.", title=target.stem[:64])
                except Exception:
                    with target.open("rb") as fh:
                        await message.reply_document(document=fh, caption="🎵 تم استلام الملف الصوتي وإرساله كملف.")
            elif media_type == "document_video":
                with target.open("rb") as fh:
                    await message.reply_document(document=fh, caption="🎬 تم استلام الفيديو كملف بنجاح.")
            elif media_type == "document_audio":
                with target.open("rb") as fh:
                    await message.reply_document(document=fh, caption="🎵 تم استلام الملف الصوتي بنجاح.")
        except Exception:
            await message.reply_text("❌ تعذر معالجة الملف حاليًا.\n\nتأكد أن الملف صالح وحجمه لا يتجاوز 500 MB، ثم حاول مرة أخرى.")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def register_media_input(app):
    media_filter = filters.VIDEO | filters.VOICE | filters.AUDIO | filters.Document.ALL
    app.add_handler(MessageHandler(media_filter, handle_media_input), group=0)
