from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes
from delivery.policy import DeliveryPolicy

CACHE_DIR = Path(os.getenv("MEDIA_STUDIO_CACHE_DIR", "/app/data/media_studio"))
CACHE_TTL_SECONDS = 6 * 60 * 60
CACHE_MAX_BYTES = 200 * 1024 * 1024
MAX_RESULT_BYTES = 47 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 240
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
                InlineKeyboardButton("🖼️ صورة", callback_data=f"studio:thumb:{token}"),
            ],
            [
                InlineKeyboardButton("✂️ أول 15ث", callback_data=f"studio:trim:{token}:15"),
                InlineKeyboardButton("✂️ أول 30ث", callback_data=f"studio:trim:{token}:30"),
            ],
            [
                InlineKeyboardButton("🗜️ ضغط", callback_data=f"studio:compress:{token}"),
            ],
        ]
    )


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


async def _create_result(source: Path, action: str, value: str | None) -> tuple[Path, str]:
    suffix = source.suffix.lower()
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
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output),
        )
        return output, "video"

    if action == "compress":
        output = source.with_name(f"{stem}_alibot_compressed.mp4")
        await _run_ffmpeg(
            "-i", str(source),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-vf", "scale=min(720,iw):-2",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "29",
            "-c:a", "aac",
            "-b:a", "96k",
            "-movflags", "+faststart",
            str(output),
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
    delivery_policy.validate_telegram_upload(
        output,
        media_type={"photo": "image"}.get(media_type, media_type),
    )
    if media_type == "audio":
        with output.open("rb") as handle:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=handle,
                caption="🎵 تم استخراج الصوت بصيغة MP3 بواسطة AliBot.",
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
                caption="🖼️ تم استخراج الصورة المصغرة من الفيديو.",
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
                caption="🎬 تم تجهيز المقطع بواسطة AliBot.",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
                connect_timeout=60,
                pool_timeout=60,
            )


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

    source = _cached_path(user.id, token)
    if source is None:
        await query.message.reply_text(
            "⚠️ انتهت صلاحية نسخة الاستوديو لهذا الفيديو.\n"
            "أعد تحميل الفيديو ثم استخدم أدوات الاستوديو."
        )
        return

    status = {
        "mp3": "🎵 جاري استخراج الصوت...",
        "thumb": "🖼️ جاري استخراج الصورة...",
        "trim": "✂️ جاري قص المقطع...",
        "compress": "🗜️ جاري ضغط الفيديو...",
    }.get(action)
    if not status:
        return

    await query.message.reply_text(status)
    output = None
    try:
        output, media_type = await _create_result(source, action, value)
        _validate_result(output)
        await _send_result(update, context, output, media_type)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"⚠️ Media Studio failed: {type(exc).__name__}: {exc}")
        await query.message.reply_text(
            "❌ تعذر تنفيذ العملية على هذا الفيديو.\n"
            "جرّب فيديو أقصر أو اختر عملية أخرى."
        )
    finally:
        if output is not None:
            output.unlink(missing_ok=True)


def register_media_studio(app) -> None:
    app.add_handler(
        CallbackQueryHandler(
            media_studio_callback,
            pattern=r"^studio:(mp3|thumb|trim|compress):",
        )
    )
