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

_CARD_TEXTS = {
    "ar": {
        "title": "🎛️ معلومات الرابط",
        "unknown": "غير معروف",
        "unavailable": "غير متاحة",
        "duration": "المدة",
        "source": "المصدر",
        "uploader": "الناشر",
        "views": "المشاهدات",
        "quality": "الدقة المتاحة",
        "status": "الحالة",
        "partial": "تم جلب الرابط، لكن بعض المعلومات غير متاحة.",
        "ready": "الرابط جاهز للتحميل.",
        "choose": "اختر ما تريد:",
        "video": "🎥 فيديو",
        "audio": "🎵 MP3",
        "favorite": "⭐ حفظ",
        "library": "📚 مكتبتي",
        "settings": "⚙️ الإعدادات",
        "thumbnail": "🖼 الصورة المصغرة",
        "cancel": "❌ إلغاء",
        "open": "🔗 فتح الرابط",
        "analyzing": "🔎 جاري تحليل الرابط...",
        "thumbnail_unavailable": "الصورة المصغرة غير متاحة لهذا الرابط.",
        "thumbnail_sent": "تم إرسال الصورة المصغرة.",
        "thumbnail_failed": "تعذر تحميل الصورة المصغرة من المصدر.",
        "cancelled": "✅ تم إلغاء العملية.",
    },
    "en": {
        "title": "🎛️ Link information",
        "unknown": "Unknown",
        "unavailable": "Unavailable",
        "duration": "Duration",
        "source": "Source",
        "uploader": "Uploader",
        "views": "Views",
        "quality": "Available resolution",
        "status": "Status",
        "partial": "The link was detected, but some information is unavailable.",
        "ready": "The link is ready to download.",
        "choose": "Choose an action:",
        "video": "🎥 Video",
        "audio": "🎵 MP3",
        "favorite": "⭐ Save",
        "library": "📚 Library",
        "settings": "⚙️ Settings",
        "thumbnail": "🖼 Thumbnail",
        "cancel": "❌ Cancel",
        "open": "🔗 Open link",
        "analyzing": "🔎 Analyzing link...",
        "thumbnail_unavailable": "Thumbnail is not available for this link.",
        "thumbnail_sent": "Thumbnail sent.",
        "thumbnail_failed": "Could not load the thumbnail from the source.",
        "cancelled": "✅ Operation cancelled.",
    },
    "tr": {
        "title": "🎛️ Bağlantı bilgileri",
        "unknown": "Bilinmiyor",
        "unavailable": "Mevcut değil",
        "duration": "Süre",
        "source": "Kaynak",
        "uploader": "Yayıncı",
        "views": "Görüntülenme",
        "quality": "Mevcut çözünürlük",
        "status": "Durum",
        "partial": "Bağlantı algılandı, ancak bazı bilgiler alınamadı.",
        "ready": "Bağlantı indirmeye hazır.",
        "choose": "Bir işlem seçin:",
        "video": "🎥 Video",
        "audio": "🎵 MP3",
        "favorite": "⭐ Kaydet",
        "library": "📚 Kitaplığım",
        "settings": "⚙️ Ayarlar",
        "thumbnail": "🖼 Küçük resim",
        "cancel": "❌ İptal",
        "open": "🔗 Bağlantıyı aç",
        "analyzing": "🔎 Bağlantı analiz ediliyor...",
        "thumbnail_unavailable": "Bu bağlantı için küçük resim mevcut değil.",
        "thumbnail_sent": "Küçük resim gönderildi.",
        "thumbnail_failed": "Kaynak küçük resmi yüklenemedi.",
        "cancelled": "✅ İşlem iptal edildi.",
    },
    "de": {
        "title": "🎛️ Linkinformationen",
        "unknown": "Unbekannt",
        "unavailable": "Nicht verfügbar",
        "duration": "Dauer",
        "source": "Quelle",
        "uploader": "Uploader",
        "views": "Aufrufe",
        "quality": "Verfügbare Auflösung",
        "status": "Status",
        "partial": "Der Link wurde erkannt, aber einige Informationen sind nicht verfügbar.",
        "ready": "Der Link ist zum Download bereit.",
        "choose": "Aktion auswählen:",
        "video": "🎥 Video",
        "audio": "🎵 MP3",
        "favorite": "⭐ Speichern",
        "library": "📚 Bibliothek",
        "settings": "⚙️ Einstellungen",
        "thumbnail": "🖼 Vorschaubild",
        "cancel": "❌ Abbrechen",
        "open": "🔗 Link öffnen",
        "analyzing": "🔎 Link wird analysiert...",
        "thumbnail_unavailable": "Für diesen Link ist kein Vorschaubild verfügbar.",
        "thumbnail_sent": "Vorschaubild gesendet.",
        "thumbnail_failed": "Das Vorschaubild konnte nicht geladen werden.",
        "cancelled": "✅ Vorgang abgebrochen.",
    },
}


def _language(bot_module, user_id: int) -> str:
    try:
        language = bot_module.get_language(user_id)
    except Exception:
        language = None
    return language if language in _CARD_TEXTS else "ar"


def _labels(language: str) -> dict:
    return _CARD_TEXTS.get(language, _CARD_TEXTS["ar"])


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


def _duration(value, language: str = "ar") -> str:
    labels = _labels(language)
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return labels["unavailable"]
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _views(value, language: str = "ar") -> str:
    labels = _labels(language)
    try:
        views = int(value)
    except (TypeError, ValueError):
        return labels["unavailable"]
    if views < 0:
        return labels["unavailable"]
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)


def _quality(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    height = data.get("height")
    width = data.get("width")
    try:
        height = int(height) if height is not None else None
        width = int(width) if width is not None else None
    except (TypeError, ValueError):
        height = width = None
    if height and width:
        return f"{width}×{height}p"
    if height:
        return f"{height}p"
    return labels["unavailable"]


def _text(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    title = html.escape(str(data.get("title") or labels["unknown"]))
    source = html.escape(str(data.get("source") or "Other"))
    duration = html.escape(_duration(data.get("duration"), language))
    uploader = html.escape(str(data.get("uploader") or labels["unknown"]))
    views = html.escape(_views(data.get("view_count"), language))
    quality = html.escape(_quality(data, language))
    status = labels["partial"] if data.get("probe_error") else labels["ready"]
    return (
        f"{labels['title']}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎬 <b>{title}</b>\n"
        f"⏱ {labels['duration']}: {duration}\n"
        f"🌐 {labels['source']}: {source}\n"
        f"👤 {labels['uploader']}: {uploader}\n"
        f"👁 {labels['views']}: {views}\n"
        f"📐 {labels['quality']}: {quality}\n\n"
        f"ℹ️ {html.escape(status)}\n\n"
        f"{labels['choose']}\n"
    )


def _keyboard(url: str, language: str = "ar") -> InlineKeyboardMarkup:
    labels = _labels(language)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels["video"], callback_data="video_menu")],
        [InlineKeyboardButton(labels["audio"], callback_data="audio_menu")],
        [
            InlineKeyboardButton(labels["favorite"], callback_data="ux_favorite_current"),
            InlineKeyboardButton(labels["library"], callback_data="ux_library"),
        ],
        [InlineKeyboardButton(labels["settings"], callback_data="ux_settings")],
        [InlineKeyboardButton(labels["thumbnail"], callback_data="sdc_thumbnail")],
        [InlineKeyboardButton(labels["open"], url=url)],
        [InlineKeyboardButton(labels["cancel"], callback_data="sdc_cancel")],
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
        "view_count": data.get("view_count"),
        "width": data.get("width"),
        "height": data.get("height"),
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
    language = _language(bot_module, user.id)
    if not bot_module.get_language(user.id):
        await message.reply_text(
            bot_module.TEXTS["ar"]["choose_language"],
            reply_markup=bot_module.language_keyboard(),
        )
        return True

    context.user_data["video_url"] = url
    data = {
        "source": _source(url),
        "title": None,
        "duration": None,
        "uploader": None,
        "thumbnail": None,
        "view_count": None,
        "width": None,
        "height": None,
    }
    status_message = await message.reply_text(
        _labels(language)["analyzing"],
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    try:
        data.update(await _probe(url))
    except Exception as exc:
        data["probe_error"] = type(exc).__name__
    context.user_data["sdc_info"] = data
    try:
        await status_message.edit_text(
            _text(data, language),
            parse_mode="HTML",
            reply_markup=_keyboard(url, language),
        )
    except Exception:
        await message.reply_text(
            _text(data, language),
            parse_mode="HTML",
            reply_markup=_keyboard(url, language),
        )
    return True


async def _show_control(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> bool:
    return await show_control_for_url(update.message, context, url, update.effective_user)


async def _send_thumbnail(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    info = context.user_data.get("sdc_info") or {}
    thumbnail = info.get("thumbnail")
    if not thumbnail or not _public_url(thumbnail):
        bot_module = __import__("bot")
        language = _language(bot_module, query.from_user.id) if query.from_user else "ar"
        await query.answer(_labels(language)["thumbnail_unavailable"], show_alert=True)
        return

    bot_module = __import__("bot")
    language = _language(bot_module, query.from_user.id) if query.from_user else "ar"
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
        caption = str(info.get("title") or _labels(language)["thumbnail"])[:900]
        try:
            await query.message.reply_photo(photo=image, caption=caption)
        except Exception:
            image.seek(0)
            await query.message.reply_document(document=image, caption=caption)
    except Exception:
        await query.answer(_labels(language)["thumbnail_failed"], show_alert=True)
        return

    await query.answer(_labels(language)["thumbnail_sent"])


async def _restore_control_from_quality_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get("video_url") or not context.user_data.get("sdc_info"):
        return False
    query = update.callback_query
    if not query:
        return False
    bot_module = __import__("bot")
    language = _language(bot_module, query.from_user.id) if query.from_user else "ar"
    await query.answer()
    await query.edit_message_text(
        _text(context.user_data["sdc_info"], language),
        parse_mode="HTML",
        reply_markup=_keyboard(context.user_data["video_url"], language),
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
    bot_module = __import__("bot")
    language = _language(bot_module, query.from_user.id) if query.from_user else "ar"

    if data == "main_menu":
        if await _restore_control_from_quality_menu(update, context):
            raise ApplicationHandlerStop
        return

    if data != "sdc_thumbnail":
        await query.answer()

    if data == "sdc_cancel":
        context.user_data.pop("video_url", None)
        context.user_data.pop("sdc_info", None)
        await query.edit_message_text(_labels(language)["cancelled"])
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
