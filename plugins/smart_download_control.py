"""Smart Download Control user UX."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
from io import BytesIO
import socket
from urllib.parse import urlparse
from urllib.request import Request

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes, MessageHandler, filters

PROBE_TIMEOUT = 25
THUMBNAIL_TIMEOUT = 20
THUMBNAIL_MAX_BYTES = 10 * 1024 * 1024


def _public_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return False
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        if not addresses:
            return False
        return all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False


def _source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    labels = {
        "youtube.com": "YouTube", "youtu.be": "YouTube",
        "instagram.com": "Instagram", "tiktok.com": "TikTok",
        "facebook.com": "Facebook", "fb.watch": "Facebook",
        "x.com": "X / Twitter", "twitter.com": "X / Twitter",
        "reddit.com": "Reddit",
    }
    for domain, label in labels.items():
        if host == domain or host.endswith("." + domain):
            return label
    return host or "Other"


def _duration(value) -> str:
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "غير متاحة"
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _text(data: dict) -> str:
    title = html.escape(str(data.get("title") or "غير معروف"))
    source = html.escape(str(data.get("source") or "Other"))
    duration = html.escape(_duration(data.get("duration")))
    uploader = html.escape(str(data.get("uploader") or "غير معروف"))
    return (
        "🎛️ <b>Smart Download Control</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎬 <b>{title}</b>\n"
        f"⏱ المدة: {duration}\n"
        f"🌐 المصدر: {source}\n"
        f"👤 الناشر: {uploader}\n\n"
        "اختر ما تريد:\n"
    )


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 فيديو", callback_data="video_menu")],
        [InlineKeyboardButton("🎵 MP3", callback_data="audio_menu")],
        [
            InlineKeyboardButton("⭐ حفظ", callback_data="ux_favorite_current"),
            InlineKeyboardButton("📚 مكتبتي", callback_data="ux_library"),
        ],
        [InlineKeyboardButton("⚙️ الإعدادات", callback_data="ux_settings")],
        [InlineKeyboardButton("🖼 الصورة المصغرة", callback_data="sdc_thumbnail")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="sdc_cancel")],
    ])


async def _probe(url: str) -> dict:
    command = [
        "python", "-m", "yt_dlp", "--no-playlist", "--skip-download",
        "--dump-single-json", "--no-warnings", "--socket-timeout", "15", url,
    ]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise
    if process.returncode != 0:
        raise RuntimeError("metadata probe failed")
    data = json.loads(stdout.decode("utf-8", errors="ignore"))
    return {
        "title": data.get("title"),
        "duration": data.get("duration"),
        "uploader": data.get("uploader") or data.get("channel"),
        "thumbnail": data.get("thumbnail"),
        "source": _source(url),
    }


async def show_control_for_url(message, context: ContextTypes.DEFAULT_TYPE, url: str, user=None) -> bool:
    """Show the canonical Smart Download Control for an already selected URL.

    This is the single handoff used by both direct URL messages and Smart Search
    selections, so search results cannot enter a second download-control UX.
    """
    if not message or not user:
        return False
    bot_module = __import__("bot")
    if user.id == getattr(bot_module, "ADMIN_ID", None) and any(
        context.user_data.get(key)
        for key in ("waiting_broadcast", "waiting_user_message", "waiting_admin_search")
    ):
        return False
    bot_module.register_user(user)
    if bot_module.is_banned(user.id):
        await message.reply_text(bot_module.TEXTS["ar"]["banned"])
        return True
    language = bot_module.get_language(user.id)
    if not language:
        await message.reply_text(
            bot_module.TEXTS["ar"]["choose_language"],
            reply_markup=bot_module.language_keyboard(),
        )
        return True
    context.user_data["video_url"] = url
    data = {"source": _source(url), "title": None, "duration": None, "uploader": None, "thumbnail": None}
    await message.reply_text("🔎 جاري تحليل الرابط...", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    try:
        data.update(await _probe(url))
    except Exception as exc:
        data["probe_error"] = type(exc).__name__
    context.user_data["sdc_info"] = data
    await message.reply_text(_text(data), parse_mode="HTML", reply_markup=_keyboard())
    return True


async def _show_control(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> bool:
    return await show_control_for_url(update.message, context, url, update.effective_user)


async def _send_thumbnail(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    info = context.user_data.get("sdc_info") or {}
    thumbnail = info.get("thumbnail")
    if not thumbnail or not _public_url(thumbnail):
        await query.answer("الصورة المصغرة غير متاحة لهذا الرابط.", show_alert=True)
        return

    bot_module = __import__("bot")
    try:
        request = Request(
            thumbnail,
            headers={
                "User-Agent": "AliBot/1.0",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        with bot_module.safe_urlopen(
            request,
            timeout=THUMBNAIL_TIMEOUT,
            max_bytes=THUMBNAIL_MAX_BYTES,
            expected_content_types={
                "image/jpeg",
                "image/png",
                "image/webp",
                "image/gif",
                "image/avif",
            },
        ) as response:
            image_bytes = bot_module.read_limited(response, THUMBNAIL_MAX_BYTES)

        if not image_bytes:
            raise ValueError("empty thumbnail response")

        image = BytesIO(image_bytes)
        image.name = "thumbnail.jpg"
        caption = str(info.get("title") or "الصورة المصغرة")[:900]
        try:
            await query.message.reply_photo(photo=image, caption=caption)
        except Exception:
            image.seek(0)
            await query.message.reply_document(document=image, caption=caption)
    except Exception:
        await query.answer("تعذر تحميل الصورة المصغرة من المصدر.", show_alert=True)
        return

    await query.answer("تم إرسال الصورة المصغرة.")


async def _restore_control_from_quality_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get("video_url") or not context.user_data.get("sdc_info"):
        return False
    query = update.callback_query
    if not query:
        return False
    await query.answer()
    await query.edit_message_text(
        _text(context.user_data["sdc_info"]),
        parse_mode="HTML",
        reply_markup=_keyboard(),
    )
    return True


async def url_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    url = update.message.text.strip()
    if not _public_url(url):
        return
    handled = await _show_control(update, context, url)
    if handled:
        raise ApplicationHandlerStop


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""

    if data == "main_menu":
        if await _restore_control_from_quality_menu(update, context):
            raise ApplicationHandlerStop
        return

    if data != "sdc_thumbnail":
        await query.answer()

    if data == "sdc_cancel":
        context.user_data.pop("video_url", None)
        context.user_data.pop("sdc_info", None)
        await query.edit_message_text("✅ تم إلغاء العملية.")
        raise ApplicationHandlerStop

    if data == "sdc_thumbnail":
        await _send_thumbnail(query, context)
        raise ApplicationHandlerStop


def register_smart_download_control(app) -> None:
    """Register the UX layer before the existing generic text/callback handlers."""
    if getattr(app, "_sdc_registered", False):
        return
    app._sdc_registered = True
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"^https?://"), url_message),
        group=-1,
    )
    app.add_handler(
        CallbackQueryHandler(callback, pattern=r"^(sdc_thumbnail|sdc_cancel|main_menu)$"),
        group=-2,
    )
    print("🎛️ Smart Download Control: ENABLED", flush=True)


__all__ = ["register_smart_download_control", "show_control_for_url"]
