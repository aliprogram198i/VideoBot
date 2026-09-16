"""Smart Download Control user UX.

This plugin sits above the existing downloader. It performs a bounded metadata
probe before download, stores only the current URL/options in user_data, and
reuses the existing video/audio callback pipeline for actual media delivery.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import socket
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

PROBE_TIMEOUT = 25


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
        [InlineKeyboardButton("⚡ تلقائي", callback_data="sdc_auto")],
        [InlineKeyboardButton("🎚 الجودة", callback_data="sdc_quality")],
        [InlineKeyboardButton("🖼 الصورة المصغرة", callback_data="sdc_thumbnail")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="sdc_cancel")],
    ])


def _quality_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 جودة الفيديو", callback_data="video_menu")],
        [InlineKeyboardButton("🎵 جودة الصوت", callback_data="audio_menu")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="sdc_back")],
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


async def _show_control(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    message = update.message
    if not message:
        return
    bot_module = __import__("bot")
    user = update.effective_user
    if not user:
        return
    if user.id == getattr(bot_module, "ADMIN_ID", None) and any(
        context.user_data.get(key)
        for key in ("waiting_broadcast", "waiting_user_message", "waiting_admin_search")
    ):
        return
    bot_module.register_user(user)
    if bot_module.is_banned(user.id):
        await message.reply_text(bot_module.TEXTS["ar"]["banned"])
        return
    language = bot_module.get_language(user.id)
    if not language:
        await message.reply_text(
            bot_module.TEXTS["ar"]["choose_language"],
            reply_markup=bot_module.language_keyboard(),
        )
        return
    context.user_data["video_url"] = url
    data = {"source": _source(url), "title": None, "duration": None, "uploader": None, "thumbnail": None}
    await message.reply_text("🔎 جاري تحليل الرابط...", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    try:
        data.update(await _probe(url))
    except Exception as exc:
        data["probe_error"] = type(exc).__name__
    context.user_data["sdc_info"] = data
    await message.reply_text(_text(data), parse_mode="HTML", reply_markup=_keyboard())


async def url_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    url = update.message.text.strip()
    if not _public_url(url):
        return
    await _show_control(update, context, url)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data == "sdc_cancel":
        context.user_data.pop("video_url", None)
        context.user_data.pop("sdc_info", None)
        await query.edit_message_text("✅ تم إلغاء العملية.")
        return
    if data == "sdc_quality":
        await query.edit_message_text("🎚 <b>اختر نوع الجودة</b>", parse_mode="HTML", reply_markup=_quality_keyboard())
        return
    if data == "sdc_back":
        info = context.user_data.get("sdc_info") or {"source": _source(context.user_data.get("video_url", ""))}
        await query.edit_message_text(_text(info), parse_mode="HTML", reply_markup=_keyboard())
        return
    if data == "sdc_thumbnail":
        info = context.user_data.get("sdc_info") or {}
        thumbnail = info.get("thumbnail")
        if not thumbnail or not _public_url(thumbnail):
            await query.answer("الصورة المصغرة غير متاحة لهذا الرابط.", show_alert=True)
            return
        caption = str(info.get("title") or "الصورة المصغرة")[:900]
        try:
            await query.message.reply_photo(photo=thumbnail, caption=caption)
        except Exception:
            await query.answer("تعذر إرسال الصورة المصغرة.", show_alert=True)
        return
    if data == "sdc_auto":
        url = context.user_data.get("video_url")
        if not url:
            await query.edit_message_text("❌ انتهت صلاحية الرابط. أرسل الرابط من جديد.")
            return
        bot_module = __import__("bot")
        context.user_data["sdc_auto"] = True
        await query.edit_message_text(
            "⚡ <b>الوضع التلقائي</b>\n\nجاري اختيار أفضل جودة متاحة وتجهيز التحميل...",
            parse_mode="HTML",
        )
        # The existing downloader callback expects a concrete choice such as
        # video_best/audio_best. Route Auto through its existing best-video path
        # instead of inventing a second download implementation.
        original_data = query.data
        query.data = "video_best"
        try:
            await bot_module.download_media(update, context)
        finally:
            query.data = original_data
        return


def register_smart_download_control(app) -> None:
    """Register the UX layer before the existing generic text handler."""
    if getattr(app, "_sdc_registered", False):
        return
    app._sdc_registered = True
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"^https?://"), url_message),
        group=-1,
    )
    app.add_handler(CallbackQueryHandler(callback, pattern=r"^sdc_(auto|quality|thumbnail|cancel|back)$"))
    print("🎛️ Smart Download Control: ENABLED", flush=True)
