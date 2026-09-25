from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from delivery.policy import DeliveryPolicy
from plugins.localization import t, language as normalize_language

CACHE_DIR = Path(os.getenv("MEDIA_STUDIO_CACHE_DIR", "/app/data/media_studio"))
CACHE_TTL_SECONDS = 6 * 60 * 60
CACHE_MAX_BYTES = 200 * 1024 * 1024
MAX_RESULT_BYTES = 47 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 240
MAX_CUSTOM_TRIM_SECONDS = 5 * 60
CUSTOM_TRIM_PATTERN = re.compile(
    r"^\s*(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}|\d+(?:\.\d+)?)"
    r"\s*(?:-|–|—|to|إلى)\s*"
    r"(?P<end>(?:\d{1,2}:)?\d{1,2}:\d{2}|\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
delivery_policy = DeliveryPolicy()


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_cache() -> None:
    _ensure_cache_dir()
    now = time.time()
    files = []
    total = 0

    for path in CACHE_DIR.glob("*"):
        if not path.is_file():
            continue
        try:
            age = now - path.stat().st_mtime
            if age > CACHE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                continue
            size = path.stat().st_size
            total += size
            files.append((path, size))
        except OSError:
            continue

    if total <= CACHE_MAX_BYTES:
        return

    for path, size in sorted(files, key=lambda item: item[0].stat().st_mtime):
        if total <= CACHE_MAX_BYTES:
            break
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            continue


def cache_media_for_user(user_id: int, source_path: str) -> str:
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    size = source.stat().st_size
    if size > MAX_RESULT_BYTES:
        raise ValueError("media_studio_cache_limit")

    _cleanup_cache()
    token = uuid.uuid4().hex[:16]
    target = CACHE_DIR / f"{int(user_id)}_{token}{source.suffix.lower() or '.bin'}"
    shutil.copy2(source, target)
    _cleanup_cache()
    return token


def _cached_path(user_id: int, token: str) -> Path | None:
    if not token or len(token) != 16 or not token.isalnum():
        return None
    _cleanup_cache()
    matches = list(CACHE_DIR.glob(f"{int(user_id)}_{token}.*"))
    return matches[0] if len(matches) == 1 and matches[0].is_file() else None


def studio_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎵 MP3", callback_data=f"studio:mp3:{token}"),
                InlineKeyboardButton("🎧 صيغ صوت", callback_data=f"studio:audio:{token}"),
            ],
            [
                InlineKeyboardButton("🖼️ صورة", callback_data=f"studio:thumb:{token}"),
                InlineKeyboardButton("🗜️ ضغط", callback_data=f"studio:compress:{token}"),
            ],
            [
                InlineKeyboardButton("✂️ أول 15ث", callback_data=f"studio:trim:{token}:15"),
                InlineKeyboardButton("✂️ أول 30ث", callback_data=f"studio:trim:{token}:30"),
            ],
            [
                InlineKeyboardButton("✂️ قص مخصص", callback_data=f"studio:trimcustom:{token}"),
                InlineKeyboardButton("📐 المقاس", callback_data=f"studio:resize:{token}"),
            ],
            [
                InlineKeyboardButton("📱 استخدام", callback_data=f"studio:preset:{token}"),
                InlineKeyboardButton("🔊 الصوت", callback_data=f"studio:volume:{token}"),
            ],
        ]
    )


def _keyboard_audio(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎵 MP3 320k", callback_data=f"studio:audio:{token}:mp3"),
                InlineKeyboardButton("🎧 M4A 192k", callback_data=f"studio:audio:{token}:m4a"),
            ],
            [
                InlineKeyboardButton("🎧 OPUS 160k", callback_data=f"studio:audio:{token}:opus"),
            ],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"studio:back:{token}")],
        ]
    )


def _keyboard_resize(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💎 1080p", callback_data=f"studio:resize:{token}:1080"),
                InlineKeyboardButton("📺 720p", callback_data=f"studio:resize:{token}:720"),
            ],
            [
                InlineKeyboardButton("📱 480p", callback_data=f"studio:resize:{token}:480"),
                InlineKeyboardButton("📲 360p", callback_data=f"studio:resize:{token}:360"),
            ],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"studio:back:{token}")],
        ]
    )


def _keyboard_presets(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎵 TikTok", callback_data=f"studio:preset:{token}:tiktok"),
                InlineKeyboardButton("📸 Reels", callback_data=f"studio:preset:{token}:reels"),
            ],
            [
                InlineKeyboardButton("▶️ Shorts", callback_data=f"studio:preset:{token}:shorts"),
                InlineKeyboardButton("📱 WhatsApp", callback_data=f"studio:preset:{token}:whatsapp"),
            ],
            [
                InlineKeyboardButton("✈️ Telegram", callback_data=f"studio:preset:{token}:telegram"),
            ],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"studio:back:{token}")],
        ]
    )


def _keyboard_volume(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔉 50%", callback_data=f"studio:volume:{token}:50"),
                InlineKeyboardButton("🔊 100%", callback_data=f"studio:volume:{token}:100"),
            ],
            [
                InlineKeyboardButton("🔊 150%", callback_data=f"studio:volume:{token}:150"),
                InlineKeyboardButton("🔊 200%", callback_data=f"studio:volume:{token}:200"),
            ],
            [InlineKeyboardButton("🔇 كتم الصوت", callback_data=f"studio:volume:{token}:0")],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"studio:back:{token}")],
        ]
    )


def _parse_timecode(value: str) -> float:
    value = value.strip()
    if ":" not in value:
        seconds = float(value)
    else:
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 2:
            minutes, seconds_part = parts
            if seconds_part >= 60:
                raise ValueError("media_studio_invalid_timecode")
            seconds = minutes * 60 + seconds_part
        elif len(parts) == 3:
            hours, minutes, seconds_part = parts
            if minutes >= 60 or seconds_part >= 60:
                raise ValueError("media_studio_invalid_timecode")
            seconds = hours * 3600 + minutes * 60 + seconds_part
        else:
            raise ValueError("media_studio_invalid_timecode")

    if seconds < 0:
        raise ValueError("media_studio_invalid_timecode")
    return seconds


def _parse_custom_trim(text: str) -> tuple[float, float]:
    match = CUSTOM_TRIM_PATTERN.match(text)
    if not match:
        raise ValueError("media_studio_invalid_trim")

    start = _parse_timecode(match.group("start"))
    end = _parse_timecode(match.group("end"))
    duration = end - start
    if duration <= 0 or duration > MAX_CUSTOM_TRIM_SECONDS:
        raise ValueError("media_studio_trim_limit")
    return start, duration


async def _run_ffmpeg(*args: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *args,
        stdout=subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("media_studio_timeout")

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"media_studio_ffmpeg_failed:{detail[-500:]}")


def _validate_result(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("media_studio_empty_result")
    if path.stat().st_size > MAX_RESULT_BYTES:
        raise RuntimeError("media_studio_result_too_large")


def _video_encode_args(output: Path) -> tuple[str, ...]:
    return (
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "27",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        str(output),
    )


async def _create_result(source: Path, action: str, value: str | None) -> tuple[Path, str]:
    stem = source.stem

    if action == "mp3":
        output = source.with_name(f"{stem}_alibot.mp3")
        await _run_ffmpeg(
            "-i", str(source),
            "-map", "0:a:0?",
            "-vn",
            "-c:a", "libmp3lame",
            "-b:a", "320k",
            str(output),
        )
        return output, "audio"

    if action == "audio":
        audio_formats = {
            "mp3": ("mp3", "libmp3lame", "320k"),
            "m4a": ("m4a", "aac", "192k"),
            "opus": ("opus", "libopus", "160k"),
        }
        fmt, codec, bitrate = audio_formats.get(value or "", (None, None, None))
        if not fmt:
            raise ValueError("media_studio_unknown_audio_format")
        output = source.with_name(f"{stem}_alibot.{fmt}")
        await _run_ffmpeg(
            "-i", str(source),
            "-map", "0:a:0?",
            "-vn",
            "-c:a", codec,
            "-b:a", bitrate,
            str(output),
        )
        return output, "audio"

    if action == "thumb":
        output = source.with_name(f"{stem}_alibot.jpg")
        await _run_ffmpeg(
            "-ss", "1",
            "-i", str(source),
            "-frames:v", "1",
            "-q:v", "2",
            str(output),
        )
        return output, "photo"

    if action == "trim":
        seconds = value if value in {"15", "30"} else "15"
        output = source.with_name(f"{stem}_alibot_{seconds}s.mp4")
        await _run_ffmpeg(
            "-ss", "0",
            "-i", str(source),
            "-t", seconds,
            "-map", "0:v:0",
            "-map", "0:a:0?",
            *(_video_encode_args(output)),
        )
        return output, "video"

    if action == "trimcustom":
        if not value:
            raise ValueError("media_studio_missing_trim")
        start, duration = _parse_custom_trim(value)
        output = source.with_name(f"{stem}_alibot_custom.mp4")
        await _run_ffmpeg(
            "-ss", f"{start:.3f}",
            "-i", str(source),
            "-t", f"{duration:.3f}",
            "-map", "0:v:0",
            "-map", "0:a:0?",
            *(_video_encode_args(output)),
        )
        return output, "video"

    if action == "compress":
        output = source.with_name(f"{stem}_alibot_compressed.mp4")
        await _run_ffmpeg(
            "-i", str(source),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-vf", "scale=min(720\\,iw):-2",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "29",
            "-c:a", "aac",
            "-b:a", "96k",
            "-movflags", "+faststart",
            str(output),
        )
        return output, "video"

    if action == "resize":
        heights = {"1080", "720", "480", "360"}
        if value not in heights:
            raise ValueError("media_studio_unknown_resize")
        output = source.with_name(f"{stem}_alibot_{value}p.mp4")
        await _run_ffmpeg(
            "-i", str(source),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-vf", f"scale=-2:min({value}\\,ih)",
            *(_video_encode_args(output)),
        )
        return output, "video"

    if action == "preset":
        presets = {
            "tiktok": "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
            "reels": "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
            "shorts": "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
            "whatsapp": "scale=-2:720",
            "telegram": "scale=min(720\\,iw):-2",
        }
        filter_graph = presets.get(value or "")
        if not filter_graph:
            raise ValueError("media_studio_unknown_preset")
        output = source.with_name(f"{stem}_alibot_{value}.mp4")
        await _run_ffmpeg(
            "-i", str(source),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-vf", filter_graph,
            *(_video_encode_args(output)),
        )
        return output, "video"

    if action == "volume":
        levels = {"0": "0", "50": "0.5", "100": "1.0", "150": "1.5", "200": "2.0"}
        factor = levels.get(value or "")
        if factor is None:
            raise ValueError("media_studio_unknown_volume")
        output = source.with_name(f"{stem}_alibot_volume_{value}.mp4")
        if value == "0":
            await _run_ffmpeg(
                "-i", str(source),
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-af", "volume=0",
                *(_video_encode_args(output)),
            )
        else:
            await _run_ffmpeg(
                "-i", str(source),
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-af", f"volume={factor}",
                *(_video_encode_args(output)),
            )
        return output, "video"

    raise ValueError("media_studio_unknown_action")


async def _send_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    output: Path,
    media_type: str,
) -> None:
    chat_id = update.effective_chat.id
    bot_module = __import__("bot")
    language = normalize_language(bot_module.get_language(update.effective_user.id) if update.effective_user else None)
    delivery_policy.validate_telegram_upload(
        output,
        media_type={"photo": "image"}.get(media_type, media_type),
    )
    if media_type == "audio":
        with output.open("rb") as handle:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=handle,
                caption=t("studio", "done_audio", language),
                read_timeout=600,
                write_timeout=600,
                connect_timeout=60,
                pool_timeout=60,
            )
    elif media_type == "photo":
        with output.open("rb") as handle:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=handle,
                caption=t("studio", "done_photo", language),
                read_timeout=600,
                write_timeout=600,
                connect_timeout=60,
                pool_timeout=60,
            )
    else:
        with output.open("rb") as handle:
            await context.bot.send_video(
                chat_id=chat_id,
                video=handle,
                caption=t("studio", "done_video", language),
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
                connect_timeout=60,
                pool_timeout=60,
            )


def _status_message(action: str, language: str = "ar") -> str | None:
    keys = {
        "mp3": "mp3_status", "audio": "audio_status", "thumb": "thumb_status",
        "trim": "trim_status", "trimcustom": "custom_status", "compress": "compress_status",
        "resize": "resize_status", "preset": "preset_status", "volume": "volume_status",
    }
    key = keys.get(action)
    return t("studio", key, normalize_language(language)) if key else None


def _remember_pending(context: ContextTypes.DEFAULT_TYPE, token: str, action: str) -> None:
    context.user_data["media_studio_pending"] = {"token": token, "action": action}


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("media_studio_pending", None)


async def _run_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    token: str,
    action: str,
    value: str | None,
) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    bot_module = __import__("bot")
    language = normalize_language(bot_module.get_language(user.id))
    source = _cached_path(user.id, token)
    if source is None:
        await message.reply_text(
            t("studio", "expired", language)
        )
        return

    status = _status_message(action, language)
    if not status:
        return

    await message.reply_text(status)
    output = None
    try:
        output, media_type = await _create_result(source, action, value)
        _validate_result(output)
        await _send_result(update, context, output, media_type)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"⚠️ Media Studio failed: {type(exc).__name__}: {exc}")
        await message.reply_text(
            t("studio", "failed", language)
        )
    finally:
        if output is not None:
            output.unlink(missing_ok=True)


async def media_studio_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    parts = (query.data or "").split(":")
    if len(parts) < 3 or parts[0] != "studio":
        return

    action = parts[1]
    token = parts[2]
    value = parts[3] if len(parts) == 4 else None

    if action == "back":
        await query.message.edit_reply_markup(reply_markup=studio_keyboard(token))
        return

    if action in {"audio", "resize", "preset", "volume"} and value is None:
        keyboards = {
            "audio": (_keyboard_audio, "🎧 اختر الصيغة الصوتية:"),
            "resize": (_keyboard_resize, "📐 اختر المقاس:"),
            "preset": (_keyboard_presets, "📱 اختر الاستخدام:"),
            "volume": (_keyboard_volume, "🔊 اختر مستوى الصوت:"),
        }
        builder, prompt = keyboards[action]
        # Submenus must replace the Studio keyboard on the same message.
        # Sending a new reply would leave the main Studio buttons underneath
        # and cause the keyboards to stack when the user presses Back.
        await query.answer(prompt)
        await query.message.edit_reply_markup(reply_markup=builder(token))
        return

    if action == "trimcustom" and value is None:
        _remember_pending(context, token, action)
        await query.message.reply_text(
            "✂️ أرسل الفترة بهذا الشكل:\n"
            "00:10 - 00:40\n\n"
            "الحد الأقصى للقص المخصص: 5 دقائق."
        )
        return

    await _run_action(update, context, token, action, value)


async def media_studio_text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    pending = context.user_data.get("media_studio_pending")
    if not pending:
        return

    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return

    # A URL is a new download request, not Media Studio input. If a user
    # leaves the custom-trim prompt and sends a URL, release the pending
    # Studio state and let the canonical URL download handler process it.
    if re.match(r"^https?://", message.text.strip(), re.IGNORECASE):
        _clear_pending(context)
        return

    token = pending.get("token")
    action = pending.get("action")
    if not token or action != "trimcustom":
        _clear_pending(context)
        raise ApplicationHandlerStop

    try:
        _parse_custom_trim(message.text)
    except ValueError:
        await message.reply_text(
            "⚠️ الصيغة غير صحيحة. أرسل مثلًا: 00:10 - 00:40\n"
            "المدة القصوى 5 دقائق."
        )
        raise ApplicationHandlerStop

    _clear_pending(context)
    await _run_action(update, context, token, "trimcustom", message.text)
    raise ApplicationHandlerStop


def register_media_studio(app) -> None:
    app.add_handler(
        CallbackQueryHandler(
            media_studio_callback,
            pattern=r"^studio:(mp3|audio|thumb|trim|trimcustom|compress|resize|preset|volume|back):",
        )
    )
    app.add_handler(
        # Media Studio owns its pending text input before Smart Search Pro
        # and the legacy catch-all text router can see it. This is an
        # explicit routing boundary, not a filter heuristic, so a custom
        # trim such as "00:10 - 00:40" can never become a search query.
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            media_studio_text_handler,
        ),
        group=-3,
    )
