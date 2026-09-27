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

from downloader.multi_media import (
    MAX_MULTI_MEDIA_ITEMS,
    is_collection_candidate,
    normalize_entries,
)

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
        "likes": "الإعجابات",
        "comments": "التعليقات",
        "content_type": "نوع المحتوى",
        "video_type": "فيديو",
        "image_type": "صورة",
        "audio_type": "صوت",
        "unknown_type": "غير محدد",
        "dimensions": "الأبعاد الأصلية",
        "quality": "أعلى جودة قابلة للتحميل",
        "estimated_size": "الحجم التقريبي",
        "format": "الصيغة",
        "telegram": "Telegram",
        "telegram_ready": "مناسب للإرسال",
        "telegram_processing": "قد يحتاج معالجة",
        "status": "الحالة",
        "partial": "تم جلب الرابط، لكن بعض المعلومات غير متاحة.",
        "ready": "الرابط جاهز للتحميل.",
        "choose": "الإجراء الرئيسي:",
        "more_options": "⚙️ خيارات أخرى",
        "video": "🎥 تحميل الفيديو",
        "post": "📌 تحميل المنشور",
        "audio": "🎵 تحميل الصوت",
        "favorite": "⭐ حفظ",
        "library": "📚 مكتبتي",
        "settings": "⚙️ الإعدادات",
        "thumbnail": "🖼 الصورة المصغرة",
        "cancel": "❌ إلغاء",
        "back": "🔙 رجوع",
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
        "likes": "Likes",
        "comments": "Comments",
        "content_type": "Content type",
        "video_type": "Video",
        "image_type": "Image",
        "audio_type": "Audio",
        "unknown_type": "Unknown",
        "dimensions": "Original dimensions",
        "quality": "Highest downloadable quality",
        "estimated_size": "Estimated size",
        "format": "Format",
        "telegram": "Telegram",
        "telegram_ready": "Ready to send",
        "telegram_processing": "May need processing",
        "status": "Status",
        "partial": "The link was detected, but some information is unavailable.",
        "ready": "The link is ready to download.",
        "choose": "Choose an action:",
        "video": "🎥 Video",
        "post": "📌 Download post",
        "audio": "🎵 MP3",
        "favorite": "⭐ Save",
        "library": "📚 Library",
        "settings": "⚙️ Settings",
        "thumbnail": "🖼 Thumbnail",
        "cancel": "❌ Cancel",
        "back": "🔙 Back",
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
        "likes": "Beğeniler",
        "comments": "Yorumlar",
        "content_type": "İçerik türü",
        "video_type": "Video",
        "image_type": "Görüntü",
        "audio_type": "Ses",
        "unknown_type": "Bilinmiyor",
        "dimensions": "Orijinal boyutlar",
        "quality": "En yüksek indirilebilir kalite",
        "estimated_size": "Tahmini boyut",
        "format": "Format",
        "telegram": "Telegram",
        "telegram_ready": "Gönderime uygun",
        "telegram_processing": "İşleme gerekebilir",
        "status": "Durum",
        "partial": "Bağlantı algılandı, ancak bazı bilgiler alınamadı.",
        "ready": "Bağlantı indirmeye hazır.",
        "choose": "Ana işlem:",
        "more_options": "⚙️ Diğer seçenekler",
        "video": "🎥 Videoyu indir",
        "audio": "🎵 Sesi indir",
        "post": "📌 Gönderiyi indir",
        "favorite": "⭐ Kaydet",
        "library": "📚 Kitaplığım",
        "settings": "⚙️ Ayarlar",
        "thumbnail": "🖼 Küçük resim",
        "cancel": "❌ İptal",
        "back": "🔙 Geri",
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
        "likes": "Likes",
        "comments": "Kommentare",
        "content_type": "Inhaltstyp",
        "video_type": "Video",
        "image_type": "Bild",
        "audio_type": "Audio",
        "unknown_type": "Unbekannt",
        "dimensions": "Originalabmessungen",
        "quality": "Höchste herunterladbare Qualität",
        "estimated_size": "Geschätzte Größe",
        "format": "Format",
        "telegram": "Telegram",
        "telegram_ready": "Versandbereit",
        "telegram_processing": "Kann Verarbeitung benötigen",
        "status": "Status",
        "partial": "Der Link wurde erkannt, aber einige Informationen sind nicht verfügbar.",
        "ready": "Der Link ist zum Download bereit.",
        "choose": "Hauptaktion:",
        "more_options": "⚙️ Weitere Optionen",
        "video": "🎥 Video herunterladen",
        "audio": "🎵 Audio herunterladen",
        "post": "📌 Beitrag herunterladen",
        "favorite": "⭐ Speichern",
        "library": "📚 Bibliothek",
        "settings": "⚙️ Einstellungen",
        "thumbnail": "🖼 Vorschaubild",
        "cancel": "❌ Abbrechen",
        "back": "🔙 Zurück",
        "open": "🔗 Link öffnen",
        "analyzing": "🔎 Link wird analysiert...",
        "thumbnail_unavailable": "Für diesen Link ist kein Vorschaubild verfügbar.",
        "thumbnail_sent": "Vorschaubild gesendet.",
        "thumbnail_failed": "Das Vorschaubild konnte nicht geladen werden.",
        "cancelled": "✅ Vorgang abgebrochen.",
    },
}


_MULTI_LABELS = {
    "ar": {"count": "📦 يحتوي هذا المنشور على {count} عناصر", "all": "⬇️ تحميل الكل", "select": "☑️ اختيار عناصر محددة", "selected": "تحميل المحدد ({count})", "item": "العنصر {index}", "video": "فيديو", "image": "صورة", "audio": "صوت", "none": "اختر عنصرًا واحدًا على الأقل.", "done": "✅ انتهت معالجة المجموعة.", "back": "🔙 رجوع"},
    "en": {"count": "📦 This post contains {count} items", "all": "⬇️ Download all", "select": "☑️ Select items", "selected": "Download selected ({count})", "item": "Item {index}", "video": "video", "image": "image", "audio": "audio", "none": "Select at least one item.", "done": "✅ Collection processing finished.", "back": "🔙 Back"},
    "tr": {"count": "📦 Bu gönderi {count} öğe içeriyor", "all": "⬇️ Tümünü indir", "select": "☑️ Öğe seç", "selected": "Seçilenleri indir ({count})", "item": "Öğe {index}", "video": "video", "image": "görüntü", "audio": "ses", "none": "En az bir öğe seçin.", "done": "✅ Koleksiyon işlemi tamamlandı.", "back": "🔙 Geri"},
    "de": {"count": "📦 Dieser Beitrag enthält {count} Elemente", "all": "⬇️ Alle herunterladen", "select": "☑️ Elemente auswählen", "selected": "Ausgewählte herunterladen ({count})", "item": "Element {index}", "video": "Video", "image": "Bild", "audio": "Audio", "none": "Wählen Sie mindestens ein Element aus.", "done": "✅ Sammlung verarbeitet.", "back": "🔙 Zurück"},
}


def _multi_labels(language: str) -> dict:
    return _MULTI_LABELS.get(language, _MULTI_LABELS["ar"])


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
    return host or "Unknown"


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


def _dimensions(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    height = data.get("height")
    width = data.get("width")
    try:
        height = int(height) if height is not None else None
        width = int(width) if width is not None else None
    except (TypeError, ValueError):
        height = width = None
    if width and height:
        return f"{width}×{height}"
    return labels["unavailable"]


def _video_formats(data: dict) -> list[dict]:
    formats = data.get("formats")
    if not isinstance(formats, list):
        return []
    result = []
    for item in formats:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        vcodec = str(item.get("vcodec") or "none").lower()
        if vcodec == "none":
            continue
        try:
            width = int(item["width"]) if item.get("width") is not None else None
            height = int(item["height"]) if item.get("height") is not None else None
        except (TypeError, ValueError):
            continue
        if width and height:
            result.append(item)
    return result


def _best_video_format(data: dict) -> dict | None:
    candidates = _video_formats(data)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            int(item.get("width") or 0) * int(item.get("height") or 0),
            int(item.get("height") or 0),
            int(item.get("width") or 0),
        ),
    )


def _downloadable_quality(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    item = _best_video_format(data)
    if not item:
        return labels["unavailable"]
    return f"{int(item['width'])}×{int(item['height'])}"


def _format_name(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    item = _best_video_format(data)
    if item:
        ext = str(item.get("ext") or "").strip().lower()
        return ext.upper() if ext else labels["unavailable"]
    ext = str(data.get("ext") or "").strip().lower()
    return ext.upper() if ext else labels["unavailable"]


def _format_bytes(value, language: str = "ar") -> str:
    labels = _labels(language)
    try:
        size = float(value)
    except (TypeError, ValueError):
        return labels["unavailable"]
    if size < 0:
        return labels["unavailable"]
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return labels["unavailable"]


def _estimated_size(data: dict, language: str = "ar") -> str:
    item = _best_video_format(data)
    if item:
        value = item.get("filesize")
        if value is None:
            value = item.get("filesize_approx")
        if value is not None:
            return _format_bytes(value, language)
    value = data.get("filesize")
    if value is None:
        value = data.get("filesize_approx")
    return _format_bytes(value, language)


def _media_type(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    value = str(data.get("media_type") or "").lower()
    return {
        "video": labels["video_type"],
        "image": labels["image_type"],
        "audio": labels["audio_type"],
    }.get(value, labels["unknown_type"])


def _interaction_line(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    parts = []
    for key, icon in (("like_count", "❤️"), ("comment_count", "💬")):
        value = _views(data.get(key), language)
        if value != labels["unavailable"]:
            parts.append(f"{icon} {labels['likes' if key == 'like_count' else 'comments']}: {value}")
    return "\n".join(parts)


def _telegram_status(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    item = _best_video_format(data)
    if not item:
        return labels["unavailable"]
    value = item.get("filesize")
    if value is None:
        value = item.get("filesize_approx")
    try:
        size = float(value)
    except (TypeError, ValueError):
        return labels["unavailable"]
    return labels["telegram_ready"] if size <= 47 * 1024 * 1024 else labels["telegram_processing"]


def _text(data: dict, language: str = "ar") -> str:
    labels = _labels(language)
    title = html.escape(str(data.get("title") or labels["unknown"]))
    source = html.escape(str(data.get("source") or "Other"))
    duration = html.escape(_duration(data.get("duration"), language))
    uploader = html.escape(str(data.get("uploader") or labels["unknown"]))
    views = html.escape(_views(data.get("view_count"), language))
    interactions = _interaction_line(data, language)
    dimensions = html.escape(_dimensions(data, language))
    quality = html.escape(_downloadable_quality(data, language))
    content_type = html.escape(_media_type(data, language))
    estimated_size = html.escape(_estimated_size(data, language))
    format_name = html.escape(_format_name(data, language))
    telegram_status = html.escape(_telegram_status(data, language))
    status = labels["partial"] if data.get("probe_error") else labels["ready"]
    interaction_text = f"{interactions}\n" if interactions else ""
    return (
        f"{labels['title']}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎬 <b>{title}</b>\n"
        f"🧩 {labels['content_type']}: {content_type}\n"
        f"⏱ {labels['duration']}: {duration}\n"
        f"🌐 {labels['source']}: {source}\n"
        f"👤 {labels['uploader']}: {uploader}\n"
        f"👁 {labels['views']}: {views}\n"
        f"{interaction_text}"
        f"📐 {labels['dimensions']}: {dimensions}\n"
        f"🎚 {labels['quality']}: {quality}\n"
        f"💾 {labels['estimated_size']}: {estimated_size}\n"
        f"📦 {labels['format']}: {format_name}\n"
        f"📤 {labels['telegram']}: {telegram_status}\n\n"
        f"ℹ️ {html.escape(status)}\n\n"
        f"{labels['choose']}\n"
    )


def _keyboard(url: str, language: str = "ar", media_type: str | None = None) -> InlineKeyboardMarkup:
    labels = _labels(language)
    rows = []
    if media_type == "video":
        rows.append([
            InlineKeyboardButton(labels["video"], callback_data="video_menu"),
            InlineKeyboardButton(labels["audio"], callback_data="audio_menu"),
        ])
        rows.append([InlineKeyboardButton(labels["post"], callback_data="post_download")])
    elif media_type == "audio":
        rows.append([InlineKeyboardButton(labels["audio"], callback_data="audio_menu")])
        rows.append([InlineKeyboardButton(labels["post"], callback_data="post_download")])
    elif media_type == "image":
        rows.append([InlineKeyboardButton(labels["post"], callback_data="post_download")])
    else:
        rows.extend([
            [
                InlineKeyboardButton(labels["video"], callback_data="video_menu"),
                InlineKeyboardButton(labels["audio"], callback_data="audio_menu"),
            ],
            [InlineKeyboardButton(labels["post"], callback_data="post_download")],
        ])
    rows.extend([
        [InlineKeyboardButton(labels["more_options"], callback_data="sdc_more")],
        [InlineKeyboardButton(labels["cancel"], callback_data="sdc_cancel")],
    ])
    return InlineKeyboardMarkup(rows)


def _more_keyboard(url: str, language: str = "ar") -> InlineKeyboardMarkup:
    labels = _labels(language)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(labels["favorite"], callback_data="ux_favorite_current"),
            InlineKeyboardButton(labels["library"], callback_data="ux_library"),
        ],
        [
            InlineKeyboardButton(labels["thumbnail"], callback_data="sdc_thumbnail"),
            InlineKeyboardButton(labels["open"], url=url),
        ],
        [InlineKeyboardButton(labels["settings"], callback_data="ux_settings")],
        [InlineKeyboardButton(labels["back"], callback_data="main_menu")],
    ])


def _detect_media_type(data: dict) -> str | None:
    formats = data.get("formats")
    if isinstance(formats, list):
        if any(
            isinstance(item, dict)
            and str(item.get("vcodec") or "none").lower() != "none"
            for item in formats
        ):
            return "video"
        if any(
            isinstance(item, dict)
            and str(item.get("acodec") or "none").lower() != "none"
            for item in formats
        ):
            return "audio"

    ext = str(data.get("ext") or "").lower()
    if ext in {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic", "heif"}:
        return "image"
    if ext in {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"}:
        return "audio"
    if data.get("duration") is not None or data.get("vcodec"):
        return "video"
    return None


async def _probe_collection(url: str) -> list[dict]:
    if not is_collection_candidate(url):
        return []
    command = [
        "python", "-m", "yt_dlp", "--flat-playlist", "--skip-download",
        "--dump-single-json", "--ignore-errors", "--no-warnings",
        "--socket-timeout", "15", url,
    ]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return []
    # Instagram carousels can return exit code 1 when one or more child
    # items have no yt-dlp formats. The parent JSON can still be usable.
    # Parse stdout first; the exit code is advisory for this collection probe.
    stdout_text = stdout.decode("utf-8", errors="ignore")
    stderr_text = stderr.decode("utf-8", errors="ignore")
    try:
        data = json.loads(stdout_text)
    except json.JSONDecodeError:
        data = {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if isinstance(entries, list):
        try:
            normalized = normalize_entries(
                entries,
                url_validator=_public_url,
                max_items=MAX_MULTI_MEDIA_ITEMS,
                parent_url=url,
            )
        except Exception:
            normalized = []
        if len(normalized) >= 2:
            return [
                {"index": item.index, "url": item.url, "title": item.title, "media_type": item.media_type,
                 "duration": item.duration, "thumbnail": item.thumbnail}
                for item in normalized
            ]
    if is_collection_candidate(url):
        import re
        child_ids = re.findall(r"ERROR: \[Instagram\] ([A-Za-z0-9_-]+):", stderr_text)
        unique_ids = []
        seen_ids = set()
        for child_id in child_ids:
            if child_id not in seen_ids:
                seen_ids.add(child_id)
                unique_ids.append(child_id)
            if len(unique_ids) >= MAX_MULTI_MEDIA_ITEMS:
                break
        if len(unique_ids) >= 2:
            return [
                {
                    "index": index,
                    "url": f"https://www.instagram.com/p/{child_id}/",
                    "title": f"Instagram item {index + 1}",
                    "media_type": "video",
                    "duration": None,
                    "thumbnail": None,
                }
                for index, child_id in enumerate(unique_ids)
            ]
    return []


def _multi_text(entries: list[dict], selected: set[int], language: str) -> str:
    labels = _multi_labels(language)
    lines = [labels["count"].format(count=len(entries)), "━━━━━━━━━━━━━━━━━━", ""]
    for item in entries:
        index = int(item.get("index", 0))
        mark = "✅" if index in selected else "▫️"
        title = str(item.get("title") or labels["item"].format(index=index + 1))
        media_type = str(item.get("media_type") or "video")
        lines.append(f"{mark} {index + 1}. {html.escape(title[:70])} — {labels.get(media_type, media_type)}")
    lines += ["", labels["select"]]
    return "\n".join(lines)


def _multi_keyboard(entries: list[dict], selected: set[int], language: str) -> InlineKeyboardMarkup:
    labels = _multi_labels(language)
    rows = []
    for item in entries:
        index = int(item.get("index", 0))
        mark = "✅" if index in selected else "▫️"
        title = str(item.get("title") or labels["item"].format(index=index + 1))
        rows.append([InlineKeyboardButton(f"{mark} {index + 1} · {title[:24]}", callback_data=f"mm:t:{index}")])
    rows.append([
        InlineKeyboardButton(labels["all"], callback_data="mm:a"),
        InlineKeyboardButton(labels["selected"].format(count=len(selected)), callback_data="mm:s"),
    ])
    rows.append([InlineKeyboardButton(labels["back"], callback_data="mm:b")])
    return InlineKeyboardMarkup(rows)


async def _show_multi_control(message, context: ContextTypes.DEFAULT_TYPE, entries: list[dict], language: str) -> None:
    selected = set()
    context.user_data["multi_media"] = entries
    context.user_data["multi_selected"] = selected
    await message.reply_text(_multi_text(entries, selected, language), parse_mode="HTML", reply_markup=_multi_keyboard(entries, selected, language))


class _CallbackQueryProxy:
    def __init__(self, query, data: str) -> None:
        self._query = query
        self.data = data

    def __getattr__(self, name):
        return getattr(self._query, name)


class _UpdateProxy:
    def __init__(self, update: Update, query: _CallbackQueryProxy) -> None:
        self._update = update
        self.callback_query = query

    def __getattr__(self, name):
        return getattr(self._update, name)


class _BatchCallbackQueryProxy(_CallbackQueryProxy):
    async def answer(self, *args, **kwargs) -> None:
        return None

    async def edit_message_text(self, *args, **kwargs):
        message = self._query.message
        if message is None:
            return None
        return await message.edit_text(*args, **kwargs)


async def _download_multi_items(update: Update, context: ContextTypes.DEFAULT_TYPE, indexes: list[int]) -> None:
    query = update.callback_query
    bot_module = __import__("bot")
    entries = context.user_data.get("multi_media") or []
    valid = [entries[index] for index in indexes if isinstance(index, int) and 0 <= index < len(entries)]
    if not valid:
        language = _language(bot_module, query.from_user.id) if query.from_user else "ar"
        await query.answer(_multi_labels(language)["none"], show_alert=True)
        return

    proxy_query = _BatchCallbackQueryProxy(query, "post_download")
    proxy_update = _UpdateProxy(update, proxy_query)

    for item in valid:
        context.user_data["video_url"] = item["url"]
        await bot_module.download_media(proxy_update, context)

    language = _language(bot_module, query.from_user.id) if query.from_user else "ar"
    await query.answer(_multi_labels(language)["done"])


async def _multi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    bot_module = __import__("bot")
    language = _language(bot_module, query.from_user.id) if query.from_user else "ar"
    entries = context.user_data.get("multi_media") or []
    if not entries:
        await query.answer(_labels(language)["expired"], show_alert=True)
        raise ApplicationHandlerStop
    data = query.data or ""
    selected = set(context.user_data.get("multi_selected") or set())
    if data.startswith("mm:t:"):
        try:
            index = int(data.rsplit(":", 1)[1])
        except (TypeError, ValueError):
            await query.answer()
            raise ApplicationHandlerStop
        if index in selected:
            selected.remove(index)
        else:
            selected.add(index)
        context.user_data["multi_selected"] = selected
        await query.answer()
        await query.edit_message_text(_multi_text(entries, selected, language), parse_mode="HTML", reply_markup=_multi_keyboard(entries, selected, language))
        raise ApplicationHandlerStop
    if data == "mm:a":
        await query.answer()
        await _download_multi_items(update, context, list(range(len(entries))))
        raise ApplicationHandlerStop
    if data == "mm:s":
        await query.answer()
        await _download_multi_items(update, context, sorted(selected))
        raise ApplicationHandlerStop
    if data == "mm:b":
        context.user_data.pop("multi_media", None)
        context.user_data.pop("multi_selected", None)
        await query.answer()
        await query.edit_message_text(_text(context.user_data.get("sdc_info") or {}, language), parse_mode="HTML",
            reply_markup=_keyboard(context.user_data.get("video_url") or "", language, (context.user_data.get("sdc_info") or {}).get("media_type")))
        raise ApplicationHandlerStop


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
        "like_count": data.get("like_count"),
        "comment_count": data.get("comment_count"),
        "width": data.get("width"),
        "height": data.get("height"),
        "ext": data.get("ext"),
        "filesize": data.get("filesize"),
        "filesize_approx": data.get("filesize_approx"),
        "media_type": _detect_media_type(data),
        "formats": data.get("formats") if isinstance(data.get("formats"), list) else [],
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
        "like_count": None,
        "comment_count": None,
        "width": None,
        "height": None,
        "ext": None,
        "filesize": None,
        "filesize_approx": None,
        "media_type": None,
        "formats": [],
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
    context.user_data["media_context"] = {"url": url, "metadata": data, "studio_token": None}

    try:
        collection_entries = await _probe_collection(url)
    except Exception:
        collection_entries = []
    if len(collection_entries) >= 2:
        await _show_multi_control(message, context, collection_entries, language)
        try:
            await status_message.delete()
        except Exception:
            pass
        return True
    try:
        await status_message.edit_text(
            _text(data, language),
            parse_mode="HTML",
            reply_markup=_keyboard(url, language, data.get("media_type")),
        )
    except Exception:
        await message.reply_text(
            _text(data, language),
            parse_mode="HTML",
            reply_markup=_keyboard(url, language),
        )
        try:
            await status_message.delete()
        except Exception:
            pass
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
        reply_markup=_keyboard(context.user_data["video_url"], language, context.user_data["sdc_info"].get("media_type")),
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


async def _route_post_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route collection posts to the multi-media selector before single-item download."""
    query = update.callback_query
    url = context.user_data.get("video_url")
    if not query or not isinstance(url, str) or not is_collection_candidate(url):
        return False
    entries = await _probe_collection(url)
    if len(entries) < 2:
        return False
    bot_module = __import__("bot")
    language = _language(bot_module, query.from_user.id) if query.from_user else "ar"
    await _show_multi_control(query.message, context, entries, language)
    return True


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

    if data == "post_download":
        if is_collection_candidate(context.user_data.get("video_url") or ""):
            await query.answer()
            entries = await _probe_collection(context.user_data["video_url"])
            if len(entries) >= 2:
                await _show_multi_control(query.message, context, entries, language)
            else:
                await bot_module.download_media(update, context)
            raise ApplicationHandlerStop
        return

    if data == "sdc_cancel":
        context.user_data.pop("video_url", None)
        context.user_data.pop("sdc_info", None)
        await query.edit_message_text(_labels(language)["cancelled"])
        raise ApplicationHandlerStop

    if data == "sdc_more":
        await query.edit_message_reply_markup(reply_markup=_more_keyboard(context.user_data.get("video_url") or "", language))
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
        CallbackQueryHandler(
            _multi_callback,
            pattern=r"^mm:(?:t:\d+|a|s|b)$",
        ),
        group=-3,
    )
    app.add_handler(
        CallbackQueryHandler(callback, pattern=r"^(post_download|sdc_thumbnail|sdc_cancel|sdc_more|main_menu)$"),
        group=-2,
    )
    print("🎛️ Smart Download Control: ENABLED", flush=True)


__all__ = ["register_smart_download_control", "show_control_for_url"]
