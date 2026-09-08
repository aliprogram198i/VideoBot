"""WhatsApp-origin audio normalization layer.

Telegram does not expose WhatsApp as a separate media type. A voice recording
shared from WhatsApp normally arrives as Telegram voice/audio, so this layer
handles only those media types and converts them to MP3 without touching URL
or video download handlers.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes, MessageHandler, filters

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 47 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 300

MESSAGES = {
    "ar": {
        "processing": "🎙️ تم استلام المقطع الصوتي.\n⏳ جاري تحويله إلى MP3...",
        "done": "🎵 تم تحويل المقطع إلى MP3 بنجاح.",
        "too_large": "❌ المقطع أكبر من الحد الذي يمكن للبوت معالجته عبر Telegram.",
        "failed": "❌ تعذر تحويل المقطع الصوتي إلى MP3. حاول مرة أخرى.",
    },
    "en": {
        "processing": "🎙️ Audio received.\n⏳ Converting it to MP3...",
        "done": "🎵 The audio was converted to MP3 successfully.",
        "too_large": "❌ The audio is larger than the Telegram processing limit.",
        "failed": "❌ Could not convert the audio to MP3. Please try again.",
    },
    "tr": {
        "processing": "🎙️ Ses alındı.\n⏳ MP3'e dönüştürülüyor...",
        "done": "🎵 Ses başarıyla MP3'e dönüştürüldü.",
        "too_large": "❌ Ses dosyası Telegram işlem sınırını aşıyor.",
        "failed": "❌ Ses MP3'e dönüştürülemedi. Lütfen tekrar deneyin.",
    },
    "de": {
        "processing": "🎙️ Audiodatei erhalten.\n⏳ Wird in MP3 umgewandelt...",
        "done": "🎵 Die Audiodatei wurde erfolgreich in MP3 umgewandelt.",
        "too_large": "❌ Die Audiodatei überschreitet das Telegram-Verarbeitungslimit.",
        "failed": "❌ Die Audiodatei konnte nicht in MP3 umgewandelt werden.",
    },
}


def _language(bot_module: Any, user_id: int) -> str:
    try:
        value = bot_module.get_language(user_id)
    except Exception:
        value = None
    return value if value in MESSAGES else "ar"


def _media_info(message):
    if message.voice:
        return message.voice, "voice.ogg", "audio/ogg"
    if message.audio:
        audio = message.audio
        name = Path(audio.file_name or "audio").name
        suffix = Path(name).suffix.lower()
        if suffix not in {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".webm", ".flac"}:
            suffix = ".bin"
        return audio, f"audio{suffix}", audio.mime_type or "application/octet-stream"
    return None, None, None


async def _run_ffmpeg(input_file: str, output_file: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        input_file,
        "-map",
        "0:a:0",
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-map_metadata",
        "0",
        output_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=FFMPEG_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("ffmpeg_timeout")

    if process.returncode != 0 or not os.path.isfile(output_file):
        detail = stderr.decode("utf-8", "ignore")[-1200:]
        raise RuntimeError(f"ffmpeg_failed:{detail}")


async def whatsapp_audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    media, original_name, _mime = _media_info(message)
    if media is None:
        return

    bot_module.register_user(user)

    if bot_module.is_banned(user.id):
        try:
            await message.reply_text(bot_module.TEXTS["ar"]["banned"])
        finally:
            raise ApplicationHandlerStop

    language = _language(bot_module, user.id)
    if not bot_module.get_language(user.id):
        await message.reply_text(
            bot_module.TEXTS["ar"]["choose_language"],
            reply_markup=bot_module.language_keyboard(),
        )
        raise ApplicationHandlerStop

    file_size = int(getattr(media, "file_size", 0) or 0)
    if file_size > MAX_INPUT_BYTES:
        await message.reply_text(MESSAGES[language]["too_large"])
        raise ApplicationHandlerStop

    status = await message.reply_text(MESSAGES[language]["processing"])

    temp_dir = tempfile.mkdtemp(prefix="alibot-audio-")
    try:
        source = os.path.join(temp_dir, original_name or "audio.bin")
        output = os.path.join(temp_dir, "AliBot_Audio.mp3")

        telegram_file = await context.bot.get_file(media.file_id)
        await telegram_file.download_to_drive(custom_path=source)

        actual_size = os.path.getsize(source)
        if actual_size > MAX_INPUT_BYTES:
            raise ValueError("input_too_large")

        await _run_ffmpeg(source, output)

        if os.path.getsize(output) > MAX_OUTPUT_BYTES:
            # Re-encode at a lower bitrate before considering splitting.
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", source, "-map", "0:a:0", "-vn", "-c:a", "libmp3lame",
                "-b:a", "64k", "-ar", "44100", "-map_metadata", "0", output,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=FFMPEG_TIMEOUT_SECONDS)
            if process.returncode != 0 or not os.path.isfile(output):
                raise RuntimeError(stderr.decode("utf-8", "ignore")[-1200:])

        if os.path.getsize(output) > MAX_OUTPUT_BYTES:
            parts = await bot_module.split_audio_for_telegram(
                output,
                temp_dir,
                max_part_bytes=MAX_OUTPUT_BYTES,
            )
            for index, part in enumerate(parts, start=1):
                await message.reply_audio(
                    audio=part,
                    caption=f"{MESSAGES[language]['done']} ({index}/{len(parts)})",
                )
        else:
            await message.reply_audio(
                audio=output,
                caption=MESSAGES[language]["done"],
                title=Path(original_name or "Audio").stem[:200],
                performer="AliBot",
            )

        try:
            await status.delete()
        except Exception:
            pass

    except ValueError as exc:
        if str(exc) == "input_too_large":
            await status.edit_text(MESSAGES[language]["too_large"])
        else:
            await status.edit_text(MESSAGES[language]["failed"])
    except Exception as exc:
        print(f"WhatsApp audio conversion failed: {type(exc).__name__}", flush=True)
        try:
            await status.edit_text(MESSAGES[language]["failed"])
        except Exception:
            pass
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    raise ApplicationHandlerStop


def register_whatsapp_audio(app: Any, bot_module: Any) -> None:
    """Register one isolated voice/audio handler before legacy user handlers."""
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            lambda update, context: whatsapp_audio_handler(update, context, bot_module),
        ),
        group=-150,
    )
    print("🎙️ WhatsApp audio → MP3 layer registered", flush=True)
