from pathlib import Path
import os
import asyncio
import sqlite3
import tempfile
import shutil
import html
import re
import ipaddress
import logging
import socket
from datetime import datetime
from urllib.parse import urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest


# ============================================================
# إعدادات البوت
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

# Telegram ID الخاص بمالك البوت
ADMIN_ID = 1486412391

LOCAL_DB_FILE = "bot_stats.db"

# Railway Volume: /app/data
# ============================================================
# قاعدة البيانات
# ============================================================

VOLUME_DIR = Path("/app/data")

if VOLUME_DIR.is_dir():
    DB_FILE = str(VOLUME_DIR / "bot_stats.db")
else:
    DB_FILE = LOCAL_DB_FILE

print(f"🗄️ Database path: {DB_FILE}")

DOWNLOAD_TIMEOUT = 900
PROCESS_SHUTDOWN_TIMEOUT = 10
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_VIDEO_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_AUDIO_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_YOINKU_RESPONSE_BYTES = 1 * 1024 * 1024
MIN_FREE_SPACE_BYTES = 256 * 1024 * 1024
MAX_BROADCAST_LENGTH = 4000

logger = logging.getLogger(__name__)


def redact_url(value):
    """Return a log-safe URL without credentials, query values, or fragments."""
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        return urlunparse((parsed.scheme, host, parsed.path, "", "<redacted>" if parsed.query else "", ""))
    except Exception:
        return "<invalid-url>"


def validate_public_http_url(value, resolver=socket.getaddrinfo):
    """Reject URLs that could target local or otherwise non-public services."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local hosts are not allowed")
    try:
        addresses = resolver(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise ValueError("Host could not be resolved") from exc
    if not addresses:
        raise ValueError("Host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Non-public network addresses are not allowed")
    return parsed


class SafeRedirectHandler(HTTPRedirectHandler):
    """Validate every redirect destination before urllib follows it."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(request, *, timeout, max_bytes, expected_content_types=None):
    """Open an external URL with SSRF and response-size protections."""
    target = request.full_url if isinstance(request, Request) else request
    validate_public_http_url(target)
    response = build_opener(SafeRedirectHandler()).open(request, timeout=timeout)
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > max_bytes:
        response.close()
        raise ValueError("Response exceeds configured size limit")
    content_type = response.headers.get_content_type()
    if expected_content_types and content_type not in expected_content_types:
        response.close()
        raise ValueError("Unexpected response content type")
    return response


def read_limited(response, max_bytes):
    chunks = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("Response exceeds configured size limit")
        chunks.append(chunk)


def final_output_from_yt_dlp(stdout_text, temp_dir, extensions):
    """Use yt-dlp's explicit after_move output, never directory iteration."""
    base = os.path.realpath(temp_dir) + os.sep
    for line in reversed(stdout_text.splitlines()):
        candidate = line.strip()
        if candidate.lower().endswith(extensions) and os.path.realpath(candidate).startswith(base) and os.path.isfile(candidate):
            return candidate
    return None


async def communicate_with_cleanup(process, timeout):
    """Wait for a child process and reliably reap it on timeout/cancellation."""
    try:
        return await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=PROCESS_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise


# ============================================================
# النصوص
# ============================================================

TEXTS = {

    "ar": {

        "choose_language":
            "🎬 أهلاً وسهلاً بكم في بوت الحسيان 🤍\n\n📥 بوتكم السهل والسريع لتحميل الفيديوهات والأصوات\nمن مختلف المنصات بجودة عالية وبكل سهولة.\n\n🔗 أرسل رابط الفيديو أو الصوت، ودع الحسيان يتولى الباقي.\n\n🆓 تحميل مجاني • 🚀 سريع • 🎧 جودة عالية\n\n👨‍👩‍👧‍👦 لا تنسوا مشاركة البوت مع الأصدقاء والعائلة ❤️\n🔗 شارك الرابط ليستفيد الجميع 🌍\n\n👇 أرسل الرابط الآن وابدأ التحميل!"
            "🎬 حمّل فيديوهاتك وصوتياتك بسهولة وسرعة.\n"
            "⚡ جودة متعددة\n"
            "🎵 تحويل الفيديو إلى صوت\n"
            "🌍 دعم عدة منصات\n\n"
            "🌐 اختر لغة البوت:",

        "welcome":
            "🎬 أهلاً وسهلاً بكم في بوت الحسيان 🤍\n\n📥 بوتكم السهل والسريع لتحميل الفيديوهات والأصوات\nمن مختلف المنصات بجودة عالية وبكل سهولة.\n\n🔗 أرسل رابط الفيديو أو الصوت، ودع الحسيان يتولى الباقي.\n\n🆓 تحميل مجاني • 🚀 سريع • 🎧 جودة عالية\n\n👨‍👩‍👧‍👦 لا تنسوا مشاركة البوت مع الأصدقاء والعائلة ❤️\n🔗 شارك الرابط ليستفيد الجميع 🌍\n\n👇 أرسل الرابط الآن وابدأ التحميل!"
            "🚀 أرسل رابط الفيديو الذي تريد تحميله، "
            "وسنتولى الباقي.\n\n"
            "📺 فيديو بجودات متعددة\n"
            "🎵 تحميل الصوت MP3\n"
            "⚡ خدمة مجانية بالكامل\n\n"
            "🔗 أرسل الرابط الآن:",

        "language_saved":
            "✅ تم تغيير لغة البوت بنجاح.",

        "send_link":
            "🎬 أرسل الآن رابط الفيديو الذي تريد تحميله.",

        "received":
            "🔗 تم استلام الرابط بنجاح.\n\n"
            "👇 اختر نوع التحميل:",

        "invalid_url":
            "❌ الرابط غير صالح.\n\n"
            "يرجى إرسال رابط يبدأ بـ http:// أو https://",

        "video_quality":
            "🎥 اختر جودة الفيديو المطلوبة:",

        "audio_quality":
            "🎵 اختر جودة الصوت المطلوبة:",

        "video_type":
            "🎥 تحميل فيديو",

        "audio_type":
            "🎵 تحميل صوت",

        "best":
            "⭐ أقصى جودة متاحة",

        "free_1080":
            "💎 1080p",

        "free_720":
            "📺 720p",

        "free_480":
            "📱 480p",

        "free_360":
            "📲 360p",

        "audio_best":
            "🎧 أفضل جودة",

        "quality_320":
            "🎵 320 kbps",

        "quality_256":
            "🎵 256 kbps",

        "quality_192":
            "🎵 192 kbps",

        "quality_128":
            "🎵 128 kbps",

        "back":
            "🔙 رجوع",

        "loading":
            "⏳ جاري تحميل الملف...\n\n"
            "🌐 المنصة: {website}\n"
            "🎚 الجودة: {quality}\n\n"
            "⚡ قد تستغرق العملية بعض الوقت حسب حجم الفيديو.\n"
            "يرجى الانتظار...",

        "uploading":
            "✅ اكتمل التحميل بنجاح!\n\n"
            "📤 جاري إرسال الملف إليك...",

        "video_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "       🎬 <b>تم تحميل الفيديو بنجاح!</b>\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "👤 <b>مرحباً {username}</b> 🤍\n\n"
            "🎚 <b>الجودة:</b> {quality}\n"
            "📥 <b>الحالة:</b> جاهز للإرسال ✅\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🙏 شكراً لاستخدامك <b>بوت الحسيان</b>\n"
            "❤️ نتمنى أن تستمتع بالخدمة\n\n"
            "🔗 شارك البوت مع أصدقائك ليستفيد الجميع 🌍",

        "audio_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "       🎵 <b>تم تحميل الصوت بنجاح!</b>\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "👤 <b>مرحباً {username}</b> 🤍\n\n"
            "🎚 <b>الجودة:</b> {quality}\n"
            "📥 <b>الحالة:</b> جاهز للإرسال ✅\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🙏 شكراً لاستخدامك <b>بوت الحسيان</b>\n"
            "❤️ نتمنى أن تستمتع بالخدمة\n\n"
            "🔗 شارك البوت مع أصدقائك ليستفيد الجميع 🌍",

        "download_error":
            "❌ تعذر تحميل هذا الرابط.\n\n"
            "قد يكون الرابط غير متاح حالياً، أو أن المنصة "
            "تحتاج إلى تسجيل دخول، أو أن الفيديو غير مدعوم.",

        "file_error":
            "❌ اكتمل التحميل ولكن لم يتم العثور على الملف.",

        "general_error":
            "❌ حدث خطأ غير متوقع أثناء العملية.\n\n"
            "يرجى المحاولة مرة أخرى.",

        "expired":
            "❌ انتهت صلاحية الطلب.\n\n"
            "أرسل الرابط مرة أخرى.",

        "banned":
            "🚫 تم منعك من استخدام البوت.",

        "language_menu":
            "🌍 اختر لغة البوت:",

        "share":
            "🔗 مشاركة البوت",

        "share_text":
            "🎬 جرّب بوت التحميل المجاني لتحميل الفيديوهات والصوتيات بسهولة ❤️",
    },


    "en": {

        "choose_language":
            "🏠 Welcome to the Download Bot ❤️\n\n"
            "🎬 Download videos and audio easily.\n"
            "⚡ Multiple qualities\n"
            "🎵 Audio extraction\n"
            "🌍 Multiple platforms\n\n"
            "🌐 Choose your language:",

        "welcome":
            "🎬 Welcome to the Download Bot ❤️\n\n"
            "🚀 Send a video link and we'll handle the rest.\n\n"
            "📺 Multiple video qualities\n"
            "🎵 MP3 audio download\n"
            "⚡ Completely free\n\n"
            "🔗 Send your link:",

        "language_saved":
            "✅ Language changed successfully.",

        "send_link":
            "🎬 Send the video link now.",

        "received":
            "🔗 Link received successfully.\n\n"
            "👇 Choose the download type:",

        "invalid_url":
            "❌ Invalid URL.\n\n"
            "Please send a URL starting with http:// or https://",

        "video_quality":
            "🎥 Choose video quality:",

        "audio_quality":
            "🎵 Choose audio quality:",

        "video_type":
            "🎥 Download Video",

        "audio_type":
            "🎵 Download Audio",

        "best":
            "⭐ Maximum available quality",

        "free_1080":
            "💎 1080p",

        "free_720":
            "📺 720p",

        "free_480":
            "📱 480p",

        "free_360":
            "📲 360p",

        "audio_best":
            "🎧 Best quality",

        "quality_320":
            "🎵 320 kbps",

        "quality_256":
            "🎵 256 kbps",

        "quality_192":
            "🎵 192 kbps",

        "quality_128":
            "🎵 128 kbps",

        "back":
            "🔙 Back",

        "loading":
            "⏳ Downloading...\n\n"
            "🌐 Platform: {website}\n"
            "🎚 Quality: {quality}\n\n"
            "⚡ This may take some time depending on the video size.\n"
            "Please wait...",

        "uploading":
            "✅ Download completed!\n\n"
            "📤 Sending the file...",

        "video_done":
            "🎬 Video downloaded successfully!\n"
            "🎚 Quality: {quality}\n\n"
            "❤️ Don't forget to share the bot with your friends!",

        "audio_done":
            "🎵 Audio downloaded successfully!\n"
            "🎚 Quality: {quality}\n\n"
            "❤️ Share the bot with your friends!",

        "download_error":
            "❌ Unable to download this link.",

        "file_error":
            "❌ Download completed but the file was not found.",

        "general_error":
            "❌ An unexpected error occurred.",

        "expired":
            "❌ The request expired.\n\n"
            "Please send the link again.",

        "banned":
            "🚫 You are not allowed to use this bot.",

        "language_menu":
            "🌍 Choose your language:",

        "share":
            "🔗 Share Bot",

        "share_text":
            "🎬 Try this free video and audio downloader ❤️",
    },


    "tr": {

        "choose_language":
            "🏠 İndirme Botuna hoş geldiniz ❤️\n\n"
            "🎬 Videoları ve sesleri kolayca indirin.\n"
            "⚡ Birden fazla kalite\n"
            "🎵 Ses indirme\n"
            "🌍 Birden fazla platform\n\n"
            "🌐 Dilinizi seçin:",

        "welcome":
            "🎬 İndirme Botuna hoş geldiniz ❤️\n\n"
            "🚀 Video bağlantısını gönderin.\n\n"
            "📺 Farklı video kaliteleri\n"
            "🎵 MP3 ses indirme\n"
            "⚡ Tamamen ücretsiz\n\n"
            "🔗 Bağlantıyı gönderin:",

        "language_saved":
            "✅ Dil başarıyla değiştirildi.",

        "send_link":
            "🎬 Video bağlantısını gönderin.",

        "received":
            "🔗 Bağlantı alındı.\n\n"
            "👇 İndirme türünü seçin:",

        "invalid_url":
            "❌ Geçersiz URL.",

        "video_quality":
            "🎥 Video kalitesini seçin:",

        "audio_quality":
            "🎵 Ses kalitesini seçin:",

        "video_type":
            "🎥 Video İndir",

        "audio_type":
            "🎵 Ses İndir",

        "best":
            "⭐ Maksimum kalite",

        "free_1080":
            "💎 1080p",

        "free_720":
            "📺 720p",

        "free_480":
            "📱 480p",

        "free_360":
            "📲 360p",

        "audio_best":
            "🎧 En iyi kalite",

        "quality_320":
            "🎵 320 kbps",

        "quality_256":
            "🎵 256 kbps",

        "quality_192":
            "🎵 192 kbps",

        "quality_128":
            "🎵 128 kbps",

        "back":
            "🔙 Geri",

        "loading":
            "⏳ İndiriliyor...\n\n"
            "🌐 Platform: {website}\n"
            "🎚 Kalite: {quality}\n\n"
            "Lütfen bekleyin...",

        "uploading":
            "✅ İndirme tamamlandı!\n\n"
            "📤 Dosya gönderiliyor...",

        "video_done":
            "🎬 Video başarıyla indirildi!\n"
            "🎚 Kalite: {quality}\n\n"
            "❤️ Botu arkadaşlarınızla paylaşmayı unutmayın!",

        "audio_done":
            "🎵 Ses başarıyla indirildi!\n"
            "🎚 Kalite: {quality}\n\n"
            "❤️ Botu arkadaşlarınızla paylaşmayı unutmayın!",

        "download_error":
            "❌ Bu bağlantı indirilemedi.",

        "file_error":
            "❌ Dosya bulunamadı.",

        "general_error":
            "❌ Beklenmeyen bir hata oluştu.",

        "expired":
            "❌ İstek süresi doldu.",

        "banned":
            "🚫 Bu botu kullanmanıza izin verilmiyor.",

        "language_menu":
            "🌍 Dilinizi seçin:",

        "share":
            "🔗 Botu Paylaş",

        "share_text":
            "🎬 Ücretsiz video ve ses indirme botunu deneyin ❤️",
    },


    "de": {

        "choose_language":
            "🏠 Willkommen beim Download-Bot ❤️\n\n"
            "🎬 Videos und Audiodateien einfach herunterladen.\n"
            "⚡ Mehrere Qualitäten\n"
            "🎵 Audio herunterladen\n"
            "🌍 Mehrere Plattformen\n\n"
            "🌐 Sprache auswählen:",

        "welcome":
            "🎬 Willkommen beim Download-Bot ❤️\n\n"
            "🚀 Senden Sie einen Videolink.\n\n"
            "📺 Mehrere Videoqualitäten\n"
            "🎵 MP3-Audio\n"
            "⚡ Komplett kostenlos\n\n"
            "🔗 Link senden:",

        "language_saved":
            "✅ Sprache erfolgreich geändert.",

        "send_link":
            "🎬 Senden Sie den Videolink.",

        "received":
            "🔗 Link erhalten.\n\n"
            "👇 Download-Typ auswählen:",

        "invalid_url":
            "❌ Ungültige URL.",

        "video_quality":
            "🎥 Videoqualität auswählen:",

        "audio_quality":
            "🎵 Audioqualität auswählen:",

        "video_type":
            "🎥 Video herunterladen",

        "audio_type":
            "🎵 Audio herunterladen",

        "best":
            "⭐ Maximale Qualität",

        "free_1080":
            "💎 1080p",

        "free_720":
            "📺 720p",

        "free_480":
            "📱 480p",

        "free_360":
            "📲 360p",

        "audio_best":
            "🎧 Beste Qualität",

        "quality_320":
            "🎵 320 kbps",

        "quality_256":
            "🎵 256 kbps",

        "quality_192":
            "🎵 192 kbps",

        "quality_128":
            "🎵 128 kbps",

        "back":
            "🔙 Zurück",

        "loading":
            "⏳ Download läuft...\n\n"
            "🌐 Plattform: {website}\n"
            "🎚 Qualität: {quality}\n\n"
            "Bitte warten...",

        "uploading":
            "✅ Download abgeschlossen!\n\n"
            "📤 Datei wird gesendet...",

        "video_done":
            "🎬 Video erfolgreich heruntergeladen!\n"
            "🎚 Qualität: {quality}\n\n"
            "❤️ Vergessen Sie nicht, den Bot mit Freunden zu teilen!",

        "audio_done":
            "🎵 Audio erfolgreich heruntergeladen!\n"
            "🎚 Qualität: {quality}\n\n"
            "❤️ Teilen Sie den Bot mit Ihren Freunden!",

        "download_error":
            "❌ Dieser Link konnte nicht heruntergeladen werden.",

        "file_error":
            "❌ Datei wurde nicht gefunden.",

        "general_error":
            "❌ Ein unerwarteter Fehler ist aufgetreten.",

        "expired":
            "❌ Die Anfrage ist abgelaufen.",

        "banned":
            "🚫 Sie dürfen diesen Bot nicht verwenden.",

        "language_menu":
            "🌍 Sprache auswählen:",

        "share":
            "🔗 Bot teilen",

        "share_text":
            "🎬 Probieren Sie diesen kostenlosen Video- und Audio-Bot ❤️",
    },
}


# ============================================================
# قاعدة البيانات
# ============================================================

def get_db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")

    return conn


def column_exists(cur, table_name, column_name):
    cur.execute(
        f"PRAGMA table_info({table_name})"
    )

    columns = cur.fetchall()

    return any(
        row["name"] == column_name
        for row in columns
    )


def add_column_if_missing(
    cur,
    table_name,
    column_name,
    column_definition
):
    if not column_exists(
        cur,
        table_name,
        column_name
    ):
        cur.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} "
            f"{column_definition}"
        )


def init_db():

    conn = get_db()
    cur = conn.cursor()

    # --------------------------------------------------------
    # إنشاء جدول المستخدمين
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            country TEXT,
            latitude REAL,
            longitude REAL,
            gender TEXT,
            language TEXT DEFAULT 'ar',
            first_seen TEXT,
            last_seen TEXT,
            downloads INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
        )
    """)

    # --------------------------------------------------------
    # ترقية قاعدة البيانات القديمة تلقائياً
    # --------------------------------------------------------

    add_column_if_missing(
        cur,
        "users",
        "username",
        "TEXT"
    )

    add_column_if_missing(
        cur,
        "users",
        "first_name",
        "TEXT"
    )

    add_column_if_missing(
        cur,
        "users",
        "last_name",
        "TEXT"
    )

    add_column_if_missing(
        cur,
        "users",
        "phone",
        "TEXT"
    )

    add_column_if_missing(
        cur,
        "users",
        "country",
        "TEXT"
    )

    add_column_if_missing(
        cur,
        "users",
        "latitude",
        "REAL"
    )

    add_column_if_missing(
        cur,
        "users",
        "longitude",
        "REAL"
    )

    add_column_if_missing(
        cur,
        "users",
        "gender",
        "TEXT"
    )

    add_column_if_missing(
        cur,
        "users",
        "language",
        "TEXT DEFAULT 'ar'"
    )

    add_column_if_missing(
        cur,
        "users",
        "first_seen",
        "TEXT"
    )

    add_column_if_missing(
        cur,
        "users",
        "last_seen",
        "TEXT"
    )

    add_column_if_missing(
        cur,
        "users",
        "downloads",
        "INTEGER DEFAULT 0"
    )

    add_column_if_missing(
        cur,
        "users",
        "is_banned",
        "INTEGER DEFAULT 0"
    )

    # --------------------------------------------------------
    # جدول التحميلات
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            url TEXT,
            website TEXT,
            media_type TEXT,
            quality TEXT,
            created_at TEXT
        )
    """)

    # --------------------------------------------------------
    # جدول الإعلانات
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            message TEXT,
            sent_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    # --------------------------------------------------------
    # جدول رسائل الإعلانات المرسلة
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broadcast_id INTEGER,
            user_id INTEGER,
            message_id INTEGER,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()

    print("✅ Database initialized successfully.")


# ============================================================
# تسجيل المستخدم
# ============================================================

def register_user(user):

    if not user:
        return

    conn = get_db()
    cur = conn.cursor()

    now = datetime.now().isoformat()

    cur.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name,
            last_name,
            first_seen,
            last_seen
        )
        VALUES (?, ?, ?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            last_seen = excluded.last_seen
    """, (
        user.id,
        user.username,
        user.first_name,
        user.last_name,
        now,
        now,
    ))

    conn.commit()
    conn.close()


def get_language(user_id):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT language FROM users WHERE user_id = ?",
        (user_id,)
    )

    row = cur.fetchone()

    conn.close()

    if row and row["language"] in TEXTS:
        return row["language"]

    return None


def set_language(user_id, language):

    if language not in TEXTS:
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET language = ?
        WHERE user_id = ?
    """, (
        language,
        user_id,
    ))

    conn.commit()
    conn.close()


def is_banned(user_id):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT is_banned
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    conn.close()

    return bool(
        row and row["is_banned"]
    )


# ============================================================
# الهاتف
# ============================================================

def save_phone(user_id, phone):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET phone = ?
        WHERE user_id = ?
    """, (
        phone,
        user_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# الموقع
# ============================================================

def save_location(
    user_id,
    latitude,
    longitude
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET
            latitude = ?,
            longitude = ?
        WHERE user_id = ?
    """, (
        latitude,
        longitude,
        user_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# الجنس
# ============================================================

def save_gender(user_id, gender):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET gender = ?
        WHERE user_id = ?
    """, (
        gender,
        user_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# الحظر
# ============================================================

def set_banned(user_id, value):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET is_banned = ?
        WHERE user_id = ?
    """, (
        1 if value else 0,
        user_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# حذف المستخدم
# ============================================================

def delete_user(user_id):
    """Delete only this user's collected data in one transaction."""
    conn = get_db()
    try:
        with conn:
            conn.execute("DELETE FROM downloads WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM broadcast_messages WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    finally:
        conn.close()


# ============================================================
# تحديد المنصة
# ============================================================

def detect_website(url):

    host = urlparse(url).netloc.lower()

    host = host.replace(
        "www.",
        ""
    )

    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"

    if "instagram.com" in host:
        return "Instagram"

    if "tiktok.com" in host:
        return "TikTok"

    if "facebook.com" in host or "fb.watch" in host:
        return "Facebook"

    if "twitter.com" in host or "x.com" in host:
        return "X / Twitter"

    if "reddit.com" in host:
        return "Reddit"

    return host or "Other"


# ============================================================
# حفظ التحميل
# ============================================================

def save_download(
    user,
    url,
    website,
    media_type,
    quality
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO downloads (
            user_id,
            username,
            url,
            website,
            media_type,
            quality,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        user.id,
        user.username,
        url,
        website,
        media_type,
        quality,
        datetime.now().isoformat(),
    ))

    cur.execute("""
        UPDATE users
        SET downloads = downloads + 1
        WHERE user_id = ?
    """, (user.id,))

    conn.commit()
    conn.close()


# ============================================================
# اللغة
# ============================================================

def language_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🇸🇦 العربية",
                callback_data="language_ar"
            )
        ],

        [
            InlineKeyboardButton(
                "🇬🇧 English",
                callback_data="language_en"
            )
        ],

        [
            InlineKeyboardButton(
                "🇹🇷 Türkçe",
                callback_data="language_tr"
            )
        ],

        [
            InlineKeyboardButton(
                "🇩🇪 Deutsch",
                callback_data="language_de"
            )
        ],

    ])


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    # تسجيل المستخدم ومعرفة هل هو مستخدم جديد
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user.id,)
    )
    existing_user = cur.fetchone()
    conn.close()

    register_user(user)

    # إشعار مالك البوت عند دخول مستخدم جديد لأول مرة
    if existing_user is None and user.id != ADMIN_ID:
        username = f"@{user.username}" if user.username else "بدون معرف"
        full_name = " ".join(
            part for part in [user.first_name, user.last_name]
            if part
        )

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🆕 <b>مستخدم جديد!</b> 🎉\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    f"👤 <b>الاسم:</b> {html.escape(full_name or 'غير معروف')}\n"
                    f"🔗 <b>المعرف:</b> {html.escape(username)}\n"
                    f"🆔 <b>ID:</b> <code>{user.id}</code>\n\n"
                    "🤖 <b>انضم مستخدم جديد إلى البوت.</b>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"⚠️ New user notification error: {e}")

    if is_banned(user.id):

        await update.message.reply_text(
            TEXTS["ar"]["banned"]
        )

        return

    language = get_language(user.id)

    if not language:

        await update.message.reply_text(
            TEXTS["ar"]["choose_language"],
            reply_markup=language_keyboard()
        )

        return

    await update.message.reply_text(
        TEXTS[language]["welcome"]
    )


# ============================================================
# تغيير اللغة
# ============================================================

async def language_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    language = get_language(
        user.id
    ) or "ar"

    await update.message.reply_text(
        TEXTS[language]["language_menu"],
        reply_markup=language_keyboard()
    )


async def language_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    language = query.data.replace(
        "language_",
        ""
    )

    if language not in TEXTS:
        return

    register_user(user)

    set_language(
        user.id,
        language
    )

    await query.edit_message_text(
        TEXTS[language]["language_saved"]
        + "\n\n"
        + TEXTS[language]["send_link"]
    )


# ============================================================
# استقبال الروابط
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    register_user(user)

    if is_banned(user.id):

        await update.message.reply_text(
            TEXTS["ar"]["banned"]
        )

        return

    language = get_language(user.id)

    if not language:

        await update.message.reply_text(
            TEXTS["ar"]["choose_language"],
            reply_markup=language_keyboard()
        )

        return

    text = update.message.text

    if not text:
        return

    url = text.strip()

    try:
        validate_public_http_url(url)
    except ValueError:
        await update.message.reply_text(TEXTS[language]["invalid_url"])
        return

    context.user_data["video_url"] = url

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                TEXTS[language]["video_type"],
                callback_data="video_menu"
            )
        ],

        [
            InlineKeyboardButton(
                TEXTS[language]["audio_type"],
                callback_data="audio_menu"
            )
        ],

    ])

    await update.message.reply_text(
        TEXTS[language]["received"],
        reply_markup=keyboard
    )


# ============================================================
# القوائم
# ============================================================

async def show_main_menu(
    query,
    language
):

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                TEXTS[language]["video_type"],
                callback_data="video_menu"
            )
        ],

        [
            InlineKeyboardButton(
                TEXTS[language]["audio_type"],
                callback_data="audio_menu"
            )
        ],

    ])

    await query.edit_message_text(
        TEXTS[language]["received"],
        reply_markup=keyboard
    )


async def show_video_menu(
    query,
    language
):

    keyboard = [

        [
            InlineKeyboardButton(
                TEXTS[language]["best"],
                callback_data="video_best"
            )
        ],

        [
            InlineKeyboardButton(
                TEXTS[language]["free_1080"],
                callback_data="video_1080"
            ),

            InlineKeyboardButton(
                TEXTS[language]["free_720"],
                callback_data="video_720"
            ),
        ],

        [
            InlineKeyboardButton(
                TEXTS[language]["free_480"],
                callback_data="video_480"
            ),

            InlineKeyboardButton(
                TEXTS[language]["free_360"],
                callback_data="video_360"
            ),
        ],

        [
            InlineKeyboardButton(
                "🗜️ ضغط الفيديو",
                callback_data="video_compress"
            )
        ],

        [
            InlineKeyboardButton(
                TEXTS[language]["back"],
                callback_data="main_menu"
            )
        ],
    ]

    await query.edit_message_text(
        TEXTS[language]["video_quality"],
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def show_audio_menu(
    query,
    language
):

    keyboard = [

        [
            InlineKeyboardButton(
                TEXTS[language]["audio_best"],
                callback_data="audio_best"
            )
        ],

        [
            InlineKeyboardButton(
                TEXTS[language]["quality_320"],
                callback_data="audio_320"
            ),

            InlineKeyboardButton(
                TEXTS[language]["quality_256"],
                callback_data="audio_256"
            ),
        ],

        [
            InlineKeyboardButton(
                TEXTS[language]["quality_192"],
                callback_data="audio_192"
            ),

            InlineKeyboardButton(
                TEXTS[language]["quality_128"],
                callback_data="audio_128"
            ),
        ],

        [
            InlineKeyboardButton(
                TEXTS[language]["back"],
                callback_data="main_menu"
            )
        ],
    ]

    await query.edit_message_text(
        TEXTS[language]["audio_quality"],
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )



# ============================================================
# استخراج مصادر الفيديو العامة من صفحات المواقع
# ============================================================

async def extract_direct_media_urls(page_url):
    """
    محاولة اكتشاف روابط الفيديو المباشرة من صفحات المواقع
    التي لا يملك yt-dlp لها extractor مخصصًا.
    """

    def fetch_page():
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 15) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/139.0 Mobile Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

        request = Request(
            page_url,
            headers=headers
        )

        with safe_urlopen(
            request,
            timeout=30,
            max_bytes=MAX_HTML_BYTES,
            expected_content_types={"text/html", "application/xhtml+xml"},
        ) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return read_limited(response, MAX_HTML_BYTES).decode(charset, errors="ignore")

    try:

        page = await asyncio.to_thread(
            fetch_page
        )

    except Exception as e:

        print()
        print("===== DIRECT SOURCE ERROR =====")
        print(repr(e))
        print("===============================")
        print()

        return []

    # فك ترميز HTML وبعض صيغ JavaScript
    page = html.unescape(page)

    page = (
        page
        .replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("\\u003d", "=")
        .replace("\\u003F", "?")
        .replace("\\u003f", "?")
    )

    candidates = []

    # --------------------------------------------------------
    # M3U8 / HLS
    # --------------------------------------------------------

    m3u8_patterns = [
        r'https?://[^"\'\s<>\\]+\.m3u8(?:\?[^"\'\s<>\\]*)?',
        r'["\']([^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
    ]

    # --------------------------------------------------------
    # MP4
    # --------------------------------------------------------

    mp4_patterns = [
        r'https?://[^"\'\s<>\\]+\.mp4(?:\?[^"\'\s<>\\]*)?',
        r'["\']([^"\']+\.mp4(?:\?[^"\']*)?)["\']',
    ]

    for pattern in m3u8_patterns + mp4_patterns:

        for match in re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        ):

            if isinstance(match, tuple):
                match = next(
                    (x for x in match if x),
                    ""
                )

            if not match:
                continue

            match = html.unescape(match)

            if match.startswith("//"):
                match = "https:" + match

            elif match.startswith("/"):
                parsed = urlparse(page_url)

                match = (
                    f"{parsed.scheme}://"
                    f"{parsed.netloc}"
                    f"{match}"
                )

            elif not match.startswith(("http://", "https://")):
                continue

            if match not in candidates:
                candidates.append(match)

    # إعطاء الأولوية لـ HLS
    candidates.sort(
        key=lambda x: (
            0 if ".m3u8" in x.lower() else 1,
            len(x)
        )
    )

    public_candidates = []
    for candidate in candidates:
        try:
            validate_public_http_url(candidate)
            public_candidates.append(candidate)
        except ValueError:
            logger.warning("Rejected non-public direct media URL: %s", redact_url(candidate))
    return public_candidates[:20]


# ============================================================
# Yoinku API - fallback
# ============================================================

async def download_with_yoinku(url, temp_dir, is_audio=False):
    """Use Yoinku as a bounded fallback without logging credentials or signed URLs."""
    api_key = os.getenv("YOINKU_API_KEY")
    if not api_key:
        logger.warning("YOINKU_API_KEY is not configured")
        return None

    import json
    import urllib.parse

    limit = MAX_AUDIO_DOWNLOAD_BYTES if is_audio else MAX_VIDEO_DOWNLOAD_BYTES
    if shutil.disk_usage(temp_dir).free < min(limit, MIN_FREE_SPACE_BYTES):
        logger.warning("Insufficient free space for Yoinku download")
        return None
    api_url = "https://yoinku.com/api/v1/download?" + urllib.parse.urlencode({
        "url": url,
        "format": "a-320" if is_audio else "v-720",
    })
    output_file = os.path.join(temp_dir, "yoinku_download" + (".mp3" if is_audio else ".mp4"))

    def fetch():
        request = Request(api_url, headers={"x-api-key": api_key, "Accept": "application/json", "User-Agent": "VideoBot/1.0"})
        with safe_urlopen(request, timeout=30, max_bytes=MAX_YOINKU_RESPONSE_BYTES, expected_content_types={"application/json"}) as response:
            data = json.loads(read_limited(response, MAX_YOINKU_RESPONSE_BYTES).decode("utf-8"))
        direct_url = data.get("url") if isinstance(data, dict) and data.get("ok") else None
        if not direct_url:
            raise ValueError("Yoinku response did not contain a download URL")
        validate_public_http_url(direct_url)
        request = Request(direct_url, headers={"User-Agent": "VideoBot/1.0"})
        try:
            with safe_urlopen(request, timeout=60, max_bytes=limit) as response, open(output_file, "wb") as output:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("Yoinku file exceeds configured size limit")
                    output.write(chunk)
        except Exception:
            try:
                os.remove(output_file)
            except FileNotFoundError:
                pass
            raise
        if not os.path.isfile(output_file) or os.path.getsize(output_file) == 0:
            raise ValueError("Yoinku returned an empty file")
        return output_file

    for attempt in range(3):
        try:
            return await asyncio.to_thread(fetch)
        except (OSError, TimeoutError) as exc:
            logger.warning("Yoinku temporary failure on attempt %d: %s", attempt + 1, type(exc).__name__)
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
        except Exception as exc:
            logger.warning("Yoinku fallback failed: %s", type(exc).__name__)
            break
    return None

# ============================================================
# ضغط الفيديو الكبير تلقائيًا
# ============================================================

MAX_TELEGRAM_VIDEO_MB = 49
MAX_TELEGRAM_VIDEO_BYTES = MAX_TELEGRAM_VIDEO_MB * 1024 * 1024

async def get_video_size_mb(media_file):
    """
    إرجاع حجم الفيديو بالميغابايت.
    """
    try:
        if not os.path.isfile(media_file):
            return 0.0

        return os.path.getsize(media_file) / 1024 / 1024

    except Exception as e:
        print("❌ Could not read video size:")
        print(repr(e))
        return 0.0


async def compress_video_if_needed(
    media_file,
    temp_dir,
    query=None,
    language="ar",
    compression_percent=50,
):
    """
    ضغط الفيديو بشكل ذكي مع عدة محاولات.

    الهدف:
    - إبقاء الملف النهائي تحت 42 MB بهامش أمان.
    - عدم إعادة الملف الأصلي الكبير إذا فشلت عملية الضغط.
    - استخدام محاولات إضافية تلقائيًا عند الحاجة.
    """

    SAFE_MB = MAX_TELEGRAM_VIDEO_MB
    SAFE_BYTES = SAFE_MB * 1024 * 1024

    try:
        if not os.path.isfile(media_file):
            print("❌ Compression input file not found")
            return None

        original_size = os.path.getsize(media_file)

        print()
        print("===== VIDEO SIZE CHECK =====")
        print(f"Original size: {original_size / 1024 / 1024:.2f} MB")
        print(f"Safe target: {SAFE_MB} MB")
        print("============================")

        # الفيديو أصغر من الحد الآمن
        if original_size <= SAFE_BYTES:
            print("✅ Compression not required.")
            return media_file

        # --------------------------------------------------------
        # إشعار المستخدم
        # --------------------------------------------------------
        if query is not None:
            try:
                compression_message = {
                    "ar": (
                        "⏳ لحظات من فضلك...\n\n"
                        "📦 تم تحميل الفيديو بنجاح، لكن حجمه كبير قليلًا.\n\n"
                        "⚙️ جاري الآن ضغط الفيديو وتحسين حجمه ليصبح مناسبًا للإرسال عبر تيليجرام.\n\n"
                        "🎯 قد تستغرق هذه العملية بعض الوقت حسب مدة الفيديو وحجمه.\n\n"
                        "📤 بعد انتهاء المعالجة سيتم إرسال الفيديو إليك تلقائيًا.\n\n"
                        "❤️ شكرًا لصبرك، لا حاجة لإعادة إرسال الرابط."
                    ),
                    "en": (
                        "⏳ Please wait a moment...\n\n"
                        "📦 The video has been downloaded successfully, but it is too large.\n\n"
                        "⚙️ The server is compressing and optimizing the video for Telegram.\n\n"
                        "🎯 This may take some time depending on the video length and size.\n\n"
                        "📤 The video will be sent automatically when processing is complete.\n\n"
                        "❤️ Thank you for your patience. There is no need to resend the link."
                    ),
                }

                message = compression_message.get(
                    language,
                    compression_message["ar"]
                )

                await query.edit_message_text(message)

            except Exception as notify_error:
                print("⚠️ Could not update compression message:")
                print(repr(notify_error))

        if not shutil.which("ffmpeg"):
            print("❌ ffmpeg غير موجود")
            return None

        if not shutil.which("ffprobe"):
            print("❌ ffprobe غير موجود")
            return None

        # --------------------------------------------------------
        # قراءة مدة الفيديو
        # --------------------------------------------------------
        probe_command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            media_file,
        ]

        probe = await asyncio.create_subprocess_exec(
            *probe_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        probe_stdout, probe_stderr = await communicate_with_cleanup(probe, 120)

        if probe.returncode != 0:
            print("❌ Could not read video duration")
            print(probe_stderr.decode(errors="ignore")[-2000:])
            return None

        try:
            duration = float(probe_stdout.decode().strip())
        except Exception:
            duration = 0

        if duration <= 0:
            print("❌ Invalid video duration")
            return None

        print(f"Duration: {duration:.2f} seconds")
        print(f"Duration: {duration / 60:.2f} minutes")

        # --------------------------------------------------------
        # نسبة الضغط
        # --------------------------------------------------------
        try:
            compression_percent = int(compression_percent)
        except Exception:
            compression_percent = 50

        compression_percent = max(
            10,
            min(50, compression_percent)
        )

        # --------------------------------------------------------
        # الحجم المستهدف
        # --------------------------------------------------------
        requested_target = int(
            original_size * (100 - compression_percent) / 100
        )

        target_bytes = min(
            requested_target,
            SAFE_BYTES
        )

        target_bytes = max(
            target_bytes,
            5 * 1024 * 1024
        )

        print()
        print("===== SMART COMPRESSION =====")
        print(f"Compression percent: {compression_percent}%")
        print(f"Target size: {target_bytes / 1024 / 1024:.2f} MB")
        print("==============================")

        # --------------------------------------------------------
        # دالة تنفيذ ضغط واحدة
        # --------------------------------------------------------
        async def run_ffmpeg(
            input_file,
            output_file,
            video_kbps,
            audio_kbps,
            scale_width,
            preset="veryfast",
        ):
            if os.path.exists(output_file):
                try:
                    os.remove(output_file)
                except Exception:
                    pass

            scale_filter = f"scale='min({scale_width},iw)':-2"

            command = [
                "ffmpeg",
                "-y",
                "-i",
                input_file,

                "-vf",
                scale_filter,

                "-c:v",
                "libx264",

                "-preset",
                preset,

                "-b:v",
                f"{video_kbps}k",

                "-maxrate",
                f"{video_kbps}k",

                "-bufsize",
                f"{max(video_kbps * 2, 120)}k",

                "-c:a",
                "aac",

                "-b:a",
                f"{audio_kbps}k",

                "-ac",
                "2",

                "-movflags",
                "+faststart",

                output_file,
            ]

            print()
            print("===== FFMPEG COMPRESSION =====")
            print(f"Video bitrate: {video_kbps}k")
            print(f"Audio bitrate: {audio_kbps}k")
            print(f"Resolution: {scale_width}px")
            print("===============================")

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await communicate_with_cleanup(process, DOWNLOAD_TIMEOUT)

            if process.returncode != 0:
                print("❌ FFmpeg failed")
                print(stderr.decode(errors="ignore")[-5000:])
                return False

            if not os.path.isfile(output_file):
                print("❌ FFmpeg output file was not created")
                return False

            return True

        # --------------------------------------------------------
        # حساب bitrate
        # --------------------------------------------------------
        audio_bitrate = 48_000

        total_bitrate = int(
            (target_bytes * 8) / duration
        )

        video_bitrate = total_bitrate - audio_bitrate

        video_bitrate = max(
            80_000,
            video_bitrate
        )

        video_bitrate = min(
            2_000_000,
            video_bitrate
        )

        video_kbps = max(
            80,
            video_bitrate // 1000
        )

        audio_kbps = 48

        if video_kbps >= 900:
            scale_width = 854
        elif video_kbps >= 500:
            scale_width = 640
        elif video_kbps >= 250:
            scale_width = 480
        else:
            scale_width = 360

        output_file = os.path.join(
            temp_dir,
            "compressed_video.mp4"
        )

        # --------------------------------------------------------
        # المحاولة الأولى
        # --------------------------------------------------------
        success = await run_ffmpeg(
            media_file,
            output_file,
            video_kbps,
            audio_kbps,
            scale_width,
        )

        if success:
            size = os.path.getsize(output_file)

            print()
            print("===== COMPRESSION RESULT =====")
            print(f"New size: {size / 1024 / 1024:.2f} MB")
            print("==============================")

            if size <= SAFE_BYTES:
                print("✅ First compression successful.")
                return output_file

        # --------------------------------------------------------
        # محاولات إضافية
        # --------------------------------------------------------
        attempts = [
            {
                "name": "SECOND",
                "factor": 0.78,
                "audio": 40,
                "scale": 640,
            },
            {
                "name": "THIRD",
                "factor": 0.62,
                "audio": 32,
                "scale": 480,
            },
            {
                "name": "FINAL",
                "factor": 0.48,
                "audio": 24,
                "scale": 360,
            },
        ]

        previous_file = output_file

        for index, attempt in enumerate(attempts, start=2):

            emergency_file = os.path.join(
                temp_dir,
                f"compressed_video_attempt_{index}.mp4"
            )

            attempt_video_kbps = max(
                60,
                int(video_kbps * attempt["factor"])
            )

            print()
            print(f"===== {attempt['name']} COMPRESSION =====")
            print(f"Video bitrate: {attempt_video_kbps}k")
            print(f"Audio bitrate: {attempt['audio']}k")
            print(f"Resolution: {attempt['scale']}px")
            print("================================")

            success = await run_ffmpeg(
                previous_file,
                emergency_file,
                attempt_video_kbps,
                attempt["audio"],
                attempt["scale"],
            )

            if not success:
                continue

            current_size = os.path.getsize(emergency_file)

            print()
            print(f"===== ATTEMPT {index} RESULT =====")
            print(
                f"Size: "
                f"{current_size / 1024 / 1024:.2f} MB"
            )
            print("================================")

            if current_size <= SAFE_BYTES:
                print(
                    f"✅ Compression attempt {index} "
                    f"successful."
                )
                return emergency_file

            previous_file = emergency_file

        # --------------------------------------------------------
        # محاولة أخيرة ديناميكية حسب الحجم الفعلي
        # --------------------------------------------------------
        if os.path.isfile(previous_file):

            current_size = os.path.getsize(previous_file)

            print()
            print("===== DYNAMIC FINAL COMPRESSION =====")
            print(
                f"Current size: "
                f"{current_size / 1024 / 1024:.2f} MB"
            )

            ratio = SAFE_BYTES / current_size

            final_video_kbps = max(
                50,
                int(video_kbps * ratio * 0.85)
            )

            final_file = os.path.join(
                temp_dir,
                "compressed_video_final.mp4"
            )

            success = await run_ffmpeg(
                previous_file,
                final_file,
                final_video_kbps,
                24,
                320,
                "faster",
            )

            if success and os.path.isfile(final_file):

                final_size = os.path.getsize(final_file)

                print()
                print("===== FINAL COMPRESSION RESULT =====")
                print(
                    f"Final size: "
                    f"{final_size / 1024 / 1024:.2f} MB"
                )
                print("====================================")

                if final_size <= SAFE_BYTES:
                    print("✅ Final compression successful.")
                    return final_file

        # --------------------------------------------------------
        # فشل كامل
        # --------------------------------------------------------
        print()
        print("===== COMPRESSION FAILED =====")
        print(
            f"❌ Could not produce a file below "
            f"{SAFE_MB} MB."
        )
        print(
            "❌ Original large file will NOT be returned."
        )
        print("==============================")

        return None

    except asyncio.TimeoutError:
        print()
        print("===== COMPRESSION TIMEOUT =====")
        print("❌ FFmpeg compression timed out.")
        print("===============================")
        return None

    except Exception as e:
        print()
        print("===== COMPRESSION ERROR =====")
        print(repr(e))
        print("=============================")
        return None


async def download_with_fallback(url, temp_dir, output_template, format_option, is_audio=False):
    """Download a validated direct candidate and return yt-dlp's final path."""
    candidates = await extract_direct_media_urls(url)
    extensions = (".mp3", ".m4a", ".opus", ".aac", ".wav") if is_audio else (".mp4", ".mkv", ".webm", ".mov")
    max_size = MAX_AUDIO_DOWNLOAD_BYTES if is_audio else MAX_VIDEO_DOWNLOAD_BYTES
    for direct_url in candidates:
        command = [
            "python", "-m", "yt_dlp", "--no-playlist", "-f", format_option,
            "--retries", "5", "--fragment-retries", "5", "--socket-timeout", "60",
            "--concurrent-fragments", "2", "--max-filesize", str(max_size),
            "--print", "after_move:filepath", "--no-warnings", "-o",
            os.path.join(temp_dir, "fallback_%(id)s.%(ext)s"),
        ]
        if is_audio:
            command.extend(["-x", "--audio-format", "mp3"])
        else:
            command.extend(["--merge-output-format", "mp4"])
        command.append(direct_url)
        try:
            process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await communicate_with_cleanup(process, DOWNLOAD_TIMEOUT)
            stdout_text = stdout.decode(errors="ignore")
            stderr_text = stderr.decode(errors="ignore")
            if process.returncode == 0:
                final_path = final_output_from_yt_dlp(stdout_text, temp_dir, extensions)
                if final_path:
                    return final_path, stdout_text, stderr_text
            logger.warning("Direct fallback failed for %s", redact_url(direct_url))
        except (asyncio.TimeoutError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("Direct fallback error: %s", type(exc).__name__)
    return None, None, None


# ============================================================
# التحميل
# ============================================================


# ============================================================
# قائمة اختيار نسبة ضغط الفيديو بعد معرفة حجمه
# ============================================================

async def show_compression_menu(
    query,
    language,
    file_size_mb
):
    compression_keyboard = [

        [
            InlineKeyboardButton(
                "🟢 ضغط خفيف 10%",
                callback_data="compress_10"
            ),
            InlineKeyboardButton(
                "🟡 ضغط متوسط 20%",
                callback_data="compress_20"
            ),
        ],

        [
            InlineKeyboardButton(
                "🟠 ضغط قوي 30%",
                callback_data="compress_30"
            ),
            InlineKeyboardButton(
                "🔴 ضغط أقوى 40%",
                callback_data="compress_40"
            ),
        ],

        [
            InlineKeyboardButton(
                "🔴 ضغط قوي جدًا 50%",
                callback_data="compress_50"
            )
        ],

        [
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="video_menu"
            )
        ],
    ]

    if language == "en":
        message = (
            f"📦 Video size: {file_size_mb:.2f} MB\n\n"
            "🗜️ Choose the compression level:\n\n"
            "🟢 10% — Light compression\n"
            "🟡 20% — Medium compression\n"
            "🟠 30% — Strong compression\n"
            "🔴 40% — Stronger compression\n"
            "🔴 50% — Very strong compression\n\n"
            "⚙️ The selected compression will be applied next."
        )
    else:
        message = (
            f"📦 حجم الفيديو: {file_size_mb:.2f} MB\n\n"
            "🗜️ اختر نسبة ضغط الفيديو:\n\n"
            "🟢 10% — ضغط خفيف\n"
            "🟡 20% — ضغط متوسط\n"
            "🟠 30% — ضغط قوي\n"
            "🔴 40% — ضغط أقوى\n"
            "🔴 50% — ضغط قوي جدًا\n\n"
            "⚙️ بعد اختيار النسبة سيبدأ ضغط الفيديو."
        )

    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(
            compression_keyboard
        )
    )


async def download_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    register_user(user)

    if is_banned(user.id):

        await query.edit_message_text(
            TEXTS["ar"]["banned"]
        )

        return

    language = get_language(
        user.id
    ) or "ar"

    url = context.user_data.get(
        "video_url"
    )

    if not url:

        await query.edit_message_text(
            TEXTS[language]["expired"]
        )

        return

    choice = query.data

    # --------------------------------------------------------
    # اختيار نسبة ضغط الفيديو
    # --------------------------------------------------------

    if choice == "video_compress":

        compression_keyboard = [

            [
                InlineKeyboardButton(
                    "🟢 ضغط خفيف 10%",
                    callback_data="compress_10"
                ),
                InlineKeyboardButton(
                    "🟡 ضغط متوسط 20%",
                    callback_data="compress_20"
                ),
            ],

            [
                InlineKeyboardButton(
                    "🟠 ضغط قوي 30%",
                    callback_data="compress_30"
                ),
                InlineKeyboardButton(
                    "🔴 ضغط أقوى 40%",
                    callback_data="compress_40"
                ),
            ],

            [
                InlineKeyboardButton(
                    "🔴 ضغط قوي جدًا 50%",
                    callback_data="compress_50"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔙 رجوع",
                    callback_data="video_menu"
                )
            ],
        ]

        await query.edit_message_text(
            "🗜️ اختر نسبة ضغط الفيديو:",
            reply_markup=InlineKeyboardMarkup(
                compression_keyboard
            )
        )

        return

    # --------------------------------------------------------
    # اختيار نسبة الضغط
    # --------------------------------------------------------

    # --------------------------------------------------------
    # اختيار نسبة الضغط
    # --------------------------------------------------------

    if choice in (
        "compress_10",
        "compress_20",
        "compress_30",
        "compress_40",
        "compress_50",
    ):

        compression_percent = int(
            choice.replace("compress_", "")
        )

        media_file = context.user_data.get(
            "compression_file"
        )

        temp_dir = context.user_data.get(
            "compression_temp_dir"
        )

        quality_name = context.user_data.get(
            "compression_quality",
            "Maximum available quality"
        )

        if not media_file or not os.path.isfile(media_file):

            await query.edit_message_text(
                TEXTS[language]["file_error"]
            )

            return

        if not temp_dir or not os.path.isdir(temp_dir):

            temp_dir = os.path.dirname(media_file)

        context.user_data["compression_percent"] = (
            compression_percent
        )

        await query.edit_message_text(
            f"🗜️ تم اختيار ضغط بنسبة {compression_percent}%\n\n"
            "⚙️ جاري ضغط الفيديو الآن...\n"
            "⏳ يرجى الانتظار حتى انتهاء المعالجة."
        )

        print()
        print("===== USER SELECTED COMPRESSION =====")
        print(f"Compression: {compression_percent}%")
        print(f"Input file: {media_file}")
        print("======================================")
        print()

        media_file = await compress_video_if_needed(
            media_file,
            temp_dir,
            query=query,
            language=language,
            compression_percent=compression_percent,
        )

        if not media_file or not os.path.isfile(media_file):

            await query.edit_message_text(
                TEXTS[language]["file_error"]
            )

            return

        final_size = os.path.getsize(media_file)

        print()
        print("===== COMPRESSED FILE READY =====")
        print(f"File: {media_file}")
        print(
            f"Size: "
            f"{final_size / 1024 / 1024:.2f} MB"
        )
        print("=================================")
        print()

        try:

            await query.edit_message_text(
                TEXTS[language]["uploading"]
            )

        except Exception as upload_error:

            print("⚠️ Could not update upload message:")
            print(repr(upload_error))

        try:

            with open(
                media_file,
                "rb"
            ) as video:

                await context.bot.send_video(

                    chat_id=update.effective_chat.id,

                    video=video,

                    caption=(
                        TEXTS[language]["video_done"]
                        .format(
                            quality=quality_name,
                            username=(
                                f"@{user.username}"
                                if user.username
                                else (
                                    user.first_name
                                    or "صديقي"
                                )
                            )
                        )
                    ),

                    supports_streaming=True,

                    read_timeout=600,
                    write_timeout=600,
                    connect_timeout=60,
                    pool_timeout=60,
                )

        except Exception as send_error:

            print()
            print("===== COMPRESSED VIDEO SEND ERROR =====")
            print(repr(send_error))
            print("========================================")
            print()

            try:
                await query.edit_message_text(
                    TEXTS[language]["general_error"]
                )
            except Exception:
                pass

            return

        # ----------------------------------------------------
        # حفظ عملية التحميل
        # ----------------------------------------------------

        save_download(
            user=user,
            url=url,
            website=detect_website(url),
            media_type="video",
            quality=quality_name,
        )

        # ----------------------------------------------------
        # تنظيف بيانات الضغط والملفات المؤقتة
        # ----------------------------------------------------

        context.user_data.pop(
            "compression_file",
            None
        )

        context.user_data.pop(
            "compression_temp_dir",
            None
        )

        context.user_data.pop(
            "compression_quality",
            None
        )

        context.user_data.pop(
            "compression_percent",
            None
        )

        try:
            await query.delete_message()
        except Exception:
            pass

        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        print("✅ Compressed video sent and temp files cleaned.")

        return

    # --------------------------------------------------------
    # القوائم
    # --------------------------------------------------------

    if choice == "video_menu":

        await show_video_menu(
            query,
            language
        )

        return

    if choice == "audio_menu":

        await show_audio_menu(
            query,
            language
        )

        return

    if choice == "main_menu":

        await show_main_menu(
            query,
            language
        )

        return

    is_audio = False
    audio_quality = None

    # --------------------------------------------------------
    # الفيديو
    # --------------------------------------------------------

    if choice == "video_best":

        format_option = (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best"
        )

        quality_name = "Maximum available quality"

    elif choice == "video_1080":

        format_option = (
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080]/"
            "best"
        )

        quality_name = "1080p"

    elif choice == "video_720":

        format_option = (
            "bestvideo*[height<=720]"
            "+bestaudio/"
            "best[height<=720]/"
            "best"
        )

        quality_name = "720p"

    elif choice == "video_480":

        format_option = (
            "bestvideo*[height<=480]"
            "+bestaudio/"
            "best[height<=480]/"
            "best"
        )

        quality_name = "480p"

    elif choice == "video_360":

        format_option = (
            "bestvideo*[height<=360]"
            "+bestaudio/"
            "best[height<=360]/"
            "best"
        )

        quality_name = "360p"

    # --------------------------------------------------------
    # الصوت
    # --------------------------------------------------------

    elif choice == "audio_best":

        is_audio = True

        format_option = "bestaudio/best"

        quality_name = "Best audio"

        audio_quality = "0"

    elif choice == "audio_320":

        is_audio = True

        format_option = "bestaudio/best"

        quality_name = "320 kbps"

        audio_quality = "320K"

    elif choice == "audio_256":

        is_audio = True

        format_option = "bestaudio/best"

        quality_name = "256 kbps"

        audio_quality = "256K"

    elif choice == "audio_192":

        is_audio = True

        format_option = "bestaudio/best"

        quality_name = "192 kbps"

        audio_quality = "192K"

    elif choice == "audio_128":

        is_audio = True

        format_option = "bestaudio/best"

        quality_name = "128 kbps"

        audio_quality = "128K"

    else:

        return

    website = detect_website(url)

    await query.edit_message_text(
        TEXTS[language]["loading"].format(
            website=website,
            quality=quality_name
        )
    )

    temp_dir = tempfile.mkdtemp(prefix="videobot_")
    # Always initialize before entering try: finally must be safe on every path.
    keep_for_compression = False

    try:

        output_template = os.path.join(
            temp_dir,
            "download_%(id)s.%(ext)s"
        )

        # ----------------------------------------------------
        # استخدام Python الحالي داخل البيئة الافتراضية
        # ----------------------------------------------------

        command = [
            "python",
            "-m",
            "yt_dlp",
        ]

        # ----------------------------------------------------
        # Deno اختياري
        # ----------------------------------------------------

        if shutil.which("deno"):

            command.extend([
                "--js-runtimes",
                "deno",
            ])

        command.extend([

            "--no-playlist",

            "--extractor-args",
            "youtube:player_client=android,web",

            "-f",
            format_option,

            "--retries",
            "3",

            "--fragment-retries",
            "3",

            "--socket-timeout",
            "60",

            "--newline",

            "--no-warnings",

            "--max-filesize",
            str(MAX_AUDIO_DOWNLOAD_BYTES if is_audio else MAX_VIDEO_DOWNLOAD_BYTES),

            "--print",
            "after_move:filepath",

            "-o",
            output_template,
        ])

        # ----------------------------------------------------
        # الصوت
        # ----------------------------------------------------

        if is_audio:

            command.extend([

                "-x",

                "--audio-format",
                "mp3",

                "--audio-quality",
                audio_quality,
            ])

        else:

            command.extend([

                "--merge-output-format",
                "mp4",
            ])

        command.append(url)

        print()
        print("===== yt-dlp COMMAND =====")
        print("yt-dlp command prepared")
        print("==========================")
        print()

        # ----------------------------------------------------
        # تشغيل yt-dlp
        # ----------------------------------------------------

        process = (
            await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )

        stdout, stderr = await communicate_with_cleanup(process, DOWNLOAD_TIMEOUT)

        stdout_text = stdout.decode(
            errors="ignore"
        )

        stderr_text = stderr.decode(
            errors="ignore"
        )

        allowed_extensions = (
            (".mp3", ".m4a", ".opus", ".aac", ".wav")
            if is_audio else (".mp4", ".mkv", ".webm", ".mov")
        )
        media_file = final_output_from_yt_dlp(stdout_text, temp_dir, allowed_extensions)

        print("yt-dlp completed")

        if stderr_text:

            print()
            print("===== yt-dlp STDERR =====")
            print(stderr_text)
            print("=========================")
            print()

        if process.returncode != 0:

            print()
            print("===== PRIMARY DOWNLOAD FAILED =====")
            print(f"URL: {redact_url(url)}")
            print(f"yt-dlp return code: {process.returncode}")
            print("===== STDOUT =====")
            print(stdout_text[-5000:])
            print("===== STDERR =====")
            print(stderr_text[-5000:])
            print("===================================")
            print()

            # ------------------------------------------------
            # محاولة استخراج مصدر مباشر من صفحة الموقع
            # ------------------------------------------------

            fallback_file, _, _ = await download_with_fallback(
                url=url,
                temp_dir=temp_dir,
                output_template=output_template,
                format_option=format_option,
                is_audio=is_audio,
            )

            if fallback_file:

                media_file = fallback_file

                print()
                print("✅ FALLBACK DOWNLOAD SUCCESS")
                print(f"File: {media_file}")
                print("==============================")
                print()

            else:

                # ------------------------------------------------
                # Yoinku fallback
                # ------------------------------------------------

                yoinku_file = await download_with_yoinku(
                    url=url,
                    temp_dir=temp_dir,
                    is_audio=is_audio,
                )

                if yoinku_file:

                    media_file = yoinku_file

                    print()
                    print("===== YOINKU FALLBACK SUCCESS =====")
                    print(f"File: {media_file}")
                    print("===================================")
                    print()

                else:

                    print()
                    print("===== ALL DOWNLOAD METHODS FAILED =====")
                    print(f"URL: {redact_url(url)}")
                    print("========================================")
                    print()

                    await query.edit_message_text(
                        TEXTS[language]["download_error"]
                    )

                    return

        # ----------------------------------------------------
        # The final path is supplied by yt-dlp; fallback paths are explicit too.
        # ----------------------------------------------------

        if not media_file:

            await query.edit_message_text(
                TEXTS[language]["file_error"]
            )

            return

        # ----------------------------------------------------
        # الصوت يتم إرساله مباشرة
        # ----------------------------------------------------

        if is_audio:

            await query.edit_message_text(
                TEXTS[language]["uploading"]
            )

            print()
            print("===== FINAL AUDIO FILE =====")
            print(f"File: {media_file}")
            if os.path.isfile(media_file):
                final_size = os.path.getsize(media_file)
                print(
                    f"Audio size: "
                    f"{final_size / 1024 / 1024:.2f} MB"
                )
            print("============================")
            print()


            with open(
                media_file,
                "rb"
            ) as audio:

                await context.bot.send_audio(

                    chat_id=update.effective_chat.id,

                    audio=audio,

                    caption=(
                        TEXTS[language]["audio_done"]
                        .format(
                            quality=quality_name,
                            username=(
                                f"@{user.username}"
                                if user.username
                                else (
                                    user.first_name
                                    or "صديقي"
                                )
                            )
                        )
                    ),

                    read_timeout=600,
                    write_timeout=600,
                    connect_timeout=60,
                    pool_timeout=60,
                )

        else:

            # ----------------------------------------------------
            # معرفة حجم الفيديو قبل الضغط
            # ----------------------------------------------------

            if not os.path.isfile(media_file):

                await query.edit_message_text(
                    TEXTS[language]["file_error"]
                )

                return

            video_size_bytes = os.path.getsize(
                media_file
            )

            video_size_mb = (
                video_size_bytes
                / 1024
                / 1024
            )

            print()
            print("===== VIDEO READY =====")
            print(f"File: {media_file}")
            print(
                f"Video size: "
                f"{video_size_mb:.2f} MB"
            )
            print("=======================")
            print()

            # ----------------------------------------------------
            # إذا كان الفيديو ضمن الحد، نرسله مباشرة
            # ----------------------------------------------------

            if video_size_bytes <= MAX_TELEGRAM_VIDEO_BYTES:

                await query.edit_message_text(
                    TEXTS[language]["uploading"]
                )

                with open(
                    media_file,
                    "rb"
                ) as video:

                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=video,
                        caption=(
                            TEXTS[language]["video_done"]
                            .format(
                                quality=quality_name,
                                username=(
                                    f"@{user.username}"
                                    if user.username
                                    else (
                                        user.first_name
                                        or "صديقي"
                                    )
                                )
                            )
                        ),
                        read_timeout=600,
                        write_timeout=600,
                        connect_timeout=60,
                        pool_timeout=60,
                    )

                save_download(user, url, website, "video", quality_name)
                try:
                    await query.delete_message()
                except Exception:
                    pass
                return

            # ----------------------------------------------------
            # الفيديو أكبر من الحد
            # لا نضغط تلقائيًا
            # نعرض الحجم ونطلب من المستخدم اختيار النسبة
            # ----------------------------------------------------

            context.user_data["compression_file"] = (
                media_file
            )

            context.user_data["compression_temp_dir"] = (
                temp_dir
            )

            context.user_data["compression_quality"] = (
                quality_name
            )

            # إبقاء مجلد التحميل موجودًا حتى يختار المستخدم
            # نسبة الضغط ويكتمل الضغط في callback لاحق.
            keep_for_compression = True

            await show_compression_menu(
                query,
                language,
                video_size_mb
            )

            return

        # ----------------------------------------------------
        # حفظ التحميل
        # ----------------------------------------------------

        save_download(
            user=user,
            url=url,
            website=website,
            media_type=(
                "audio"
                if is_audio
                else "video"
            ),
            quality=quality_name,
        )

        # ----------------------------------------------------
        # حذف رسالة الأزرار
        # ----------------------------------------------------

        try:

            await query.delete_message()

        except Exception:

            pass

    except asyncio.TimeoutError:

        try:

            await query.edit_message_text(
                TEXTS[language]["download_error"]
            )

        except Exception:

            pass

    except Exception as e:

        print()
        print("===== BOT ERROR =====")
        print(repr(e))
        print("====================")
        print()

        try:

            await query.edit_message_text(
                TEXTS[language]["general_error"]
            )

        except Exception:

            pass

    finally:

        # لا تحذف الملفات إذا كان المستخدم سيختار
        # نسبة الضغط من قائمة الضغط.
        if not keep_for_compression:
            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )


# ============================================================
# لوحة الإدارة
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 لوحة المعلومات",
                callback_data="admin_dashboard_30"
            )
        ],
        [
            InlineKeyboardButton(
                "📈 الإحصائيات",
                callback_data="admin_stats"
            ),
            InlineKeyboardButton(
                "👥 المستخدمون",
                callback_data="admin_users_0"
            ),
        ],
        [
            InlineKeyboardButton(
                "📢 إرسال إعلان",
                callback_data="admin_broadcast"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑️ مسح الإعلانات المرسلة",
                callback_data="admin_delete_broadcasts"
            )
        ],
        [
            InlineKeyboardButton(
                "🧹 تفريغ ذاكرة التخزين",
                callback_data="admin_storage"
            )
        ],
    ])


async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    if user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ هذا الأمر متاح لمالك البوت فقط."
        )

        return

    await update.message.reply_text(
        "🛠️ لوحة إدارة بوت التحميل\n\n"
        "اختر القسم الذي تريد إدارته:",
        reply_markup=admin_keyboard()
    )


# ============================================================
# الإحصائيات
# ============================================================

def get_statistics():

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) AS count FROM users"
    )

    total_users = cur.fetchone()["count"]

    cur.execute("""
        SELECT COUNT(*) AS count
        FROM users
        WHERE is_banned = 1
    """)

    banned_users = cur.fetchone()["count"]

    cur.execute(
        "SELECT COUNT(*) AS count FROM downloads"
    )

    total_downloads = cur.fetchone()["count"]

    cur.execute("""
        SELECT COUNT(*) AS count
        FROM downloads
        WHERE media_type = 'video'
    """)

    total_videos = cur.fetchone()["count"]

    cur.execute("""
        SELECT COUNT(*) AS count
        FROM downloads
        WHERE media_type = 'audio'
    """)

    total_audio = cur.fetchone()["count"]

    cur.execute("""
        SELECT website, COUNT(*) AS count
        FROM downloads
        GROUP BY website
        ORDER BY count DESC
        LIMIT 10
    """)

    websites = cur.fetchall()

    cur.execute("""
        SELECT language, COUNT(*) AS count
        FROM users
        GROUP BY language
        ORDER BY count DESC
    """)

    languages = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) AS count
        FROM users
        WHERE phone IS NOT NULL
        AND phone != ''
    """)

    phones = cur.fetchone()["count"]

    cur.execute("""
        SELECT COUNT(*) AS count
        FROM users
        WHERE latitude IS NOT NULL
        AND longitude IS NOT NULL
    """)

    locations = cur.fetchone()["count"]

    cur.execute("""
        SELECT gender, COUNT(*) AS count
        FROM users
        WHERE gender IS NOT NULL
        AND gender != ''
        GROUP BY gender
    """)

    genders = cur.fetchall()

    conn.close()

    return {
        "users": total_users,
        "banned": banned_users,
        "downloads": total_downloads,
        "videos": total_videos,
        "audio": total_audio,
        "websites": websites,
        "languages": languages,
        "phones": phones,
        "locations": locations,
        "genders": genders,
    }


async def show_admin_stats(query):

    data = get_statistics()

    text = (
        "📊 إحصائيات بوت التحميل\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 إجمالي المستخدمين: "
        f"{data['users']}\n"

        f"🚫 المستخدمون المحظورون: "
        f"{data['banned']}\n\n"

        f"📥 إجمالي التحميلات: "
        f"{data['downloads']}\n"

        f"🎥 الفيديوهات: "
        f"{data['videos']}\n"

        f"🎵 الصوتيات: "
        f"{data['audio']}\n\n"

        f"📱 أرقام الهواتف: "
        f"{data['phones']}\n"

        f"📍 المواقع: "
        f"{data['locations']}\n\n"

        "🚻 الجنس:\n"
    )

    for row in data["genders"]:

        gender = row["gender"]

        text += (
            f"• {gender}: "
            f"{row['count']}\n"
        )

    text += "\n🌐 أكثر المنصات استخداماً:\n"

    for row in data["websites"]:

        text += (
            f"• {row['website']}: "
            f"{row['count']}\n"
        )

    text += "\n🌍 اللغات:\n"

    language_names = {
        "ar": "🇸🇦 العربية",
        "en": "🇬🇧 English",
        "tr": "🇹🇷 Türkçe",
        "de": "🇩🇪 Deutsch",
    }

    for row in data["languages"]:

        name = language_names.get(
            row["language"],
            row["language"]
        )

        text += (
            f"• {name}: "
            f"{row['count']}\n"
        )

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "👥 المستخدمون",
                callback_data="admin_users_0"
            )
        ],

        [
            InlineKeyboardButton(
                "📢 إعلان",
                callback_data="admin_broadcast"
            )
        ],

        [
            InlineKeyboardButton(
                "🔙 لوحة الإدارة",
                callback_data="admin_home"
            )
        ],

    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard
    )


# ============================================================
# المستخدمون
# ============================================================

async def admin_users(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:

        page = int(
            query.data.replace(
                "admin_users_",
                ""
            )
        )

    except ValueError:

        page = 0

    per_page = 10
    offset = page * per_page

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            user_id,
            username,
            first_name,
            downloads,
            is_banned
        FROM users
        ORDER BY
            downloads DESC,
            last_seen DESC
        LIMIT ? OFFSET ?
    """, (
        per_page,
        offset,
    ))

    users = cur.fetchall()

    cur.execute(
        "SELECT COUNT(*) AS count FROM users"
    )

    total_users = cur.fetchone()["count"]

    conn.close()

    keyboard = []

    for row in users:

        if row["username"]:

            display_name = (
                "@"
                + row["username"]
            )

        elif row["first_name"]:

            display_name = row["first_name"]

        else:

            display_name = (
                f"ID {row['user_id']}"
            )

        display_name = display_name[:20]

        status = (
            "🚫"
            if row["is_banned"]
            else "🟢"
        )

        keyboard.append([

            InlineKeyboardButton(

                f"{status} {display_name} "
                f"│ 📥 {row['downloads']}",

                callback_data=(
                    f"user_{row['user_id']}"
                )
            )

        ])

    navigation = []

    if page > 0:

        navigation.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"admin_users_{page - 1}"
                )
            )
        )

    if offset + per_page < total_users:

        navigation.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"admin_users_{page + 1}"
                )
            )
        )

    if navigation:

        keyboard.append(
            navigation
        )

    keyboard.append([

        InlineKeyboardButton(
            "🔍 بحث عن مستخدم",
            callback_data="admin_search"
        )

    ])

    keyboard.append([

        InlineKeyboardButton(
            "📊 الإحصائيات",
            callback_data="admin_stats"
        ),

        InlineKeyboardButton(
            "🔙 الرئيسية",
            callback_data="admin_home"
        ),

    ])

    await query.edit_message_text(

        "👥 إدارة المستخدمين\n\n"
        "🟢 مستخدم نشط\n"
        "🚫 مستخدم محظور\n\n"
        "اضغط على المستخدم لعرض التفاصيل.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# ============================================================
# تفاصيل المستخدم
# ============================================================

async def admin_user_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:

        target_id = int(
            query.data.replace(
                "user_",
                ""
            )
        )

    except ValueError:

        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM users
        WHERE user_id = ?
    """, (target_id,))

    target = cur.fetchone()

    conn.close()

    if not target:

        await query.edit_message_text(
            "❌ المستخدم غير موجود."
        )

        return

    username = (
        "@"
        + target["username"]
        if target["username"]
        else "غير محدد"
    )

    full_name = " ".join(
        filter(
            None,
            [
                target["first_name"],
                target["last_name"],
            ]
        )
    )

    full_name = (
        full_name
        if full_name
        else "غير محدد"
    )

    phone = (
        target["phone"]
        or "غير متوفر"
    )

    gender = (
        target["gender"]
        or "غير متوفر"
    )

    if target["latitude"] is not None:

        location = (
            f"{target['latitude']}, "
            f"{target['longitude']}"
        )

    else:

        location = "غير متوفر"

    language_names = {
        "ar": "🇸🇦 العربية",
        "en": "🇬🇧 English",
        "tr": "🇹🇷 Türkçe",
        "de": "🇩🇪 Deutsch",
    }

    language_name = language_names.get(
        target["language"],
        target["language"]
    )

    status = (
        "🚫 محظور"
        if target["is_banned"]
        else "🟢 نشط"
    )

    text = (

        "👤 تفاصيل المستخدم\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"🟢 الحالة: {status}\n\n"

        f"👤 الاسم: {html.escape(full_name)}\n"
        f"🔹 username: {html.escape(username)}\n"
        f"🆔 Telegram ID: {target['user_id']}\n\n"

        f"📱 الهاتف: {html.escape(phone)}\n"
        f"📍 الموقع: {html.escape(location)}\n"
        f"🚻 الجنس: {html.escape(gender)}\n\n"

        f"🌍 اللغة: {language_name}\n"
        f"📥 التحميلات: {target['downloads']}\n\n"

        f"📅 أول دخول:\n"
        f"{target['first_seen']}\n\n"

        f"🕐 آخر نشاط:\n"
        f"{target['last_seen']}"
    )

    keyboard = []

    if target["is_banned"]:

        keyboard.append([

            InlineKeyboardButton(
                "🟢 فك الحظر",
                callback_data=(
                    f"unban_{target_id}"
                )
            )

        ])

    else:

        keyboard.append([

            InlineKeyboardButton(
                "🚫 حظر المستخدم",
                callback_data=(
                    f"ban_{target_id}"
                )
            )

        ])

    keyboard.append([

        InlineKeyboardButton(
            "🔗 روابط التحميل",
            callback_data=(
                f"links_{target_id}_0"
            )
        )

    ])

    keyboard.append([

        InlineKeyboardButton(
            "📢 إرسال رسالة",
            callback_data=(
                f"message_user_{target_id}"
            )
        )

    ])

    keyboard.append([

        InlineKeyboardButton(
            "🗑️ حذف المستخدم",
            callback_data=(
                f"delete_user_{target_id}"
            )
        )

    ])

    keyboard.append([

        InlineKeyboardButton(
            "🔙 المستخدمون",
            callback_data="admin_users_0"
        )

    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# ============================================================
# الحظر
# ============================================================

async def ban_user_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer(
        "تم حظر المستخدم."
    )

    if update.effective_user.id != ADMIN_ID:
        return

    try:

        user_id = int(
            query.data.replace(
                "ban_",
                ""
            )
        )

    except ValueError:

        return

    set_banned(
        user_id,
        True
    )

    query.data = f"user_{user_id}"

    await admin_user_details(
        update,
        context
    )


async def unban_user_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer(
        "تم فك الحظر."
    )

    if update.effective_user.id != ADMIN_ID:
        return

    try:

        user_id = int(
            query.data.replace(
                "unban_",
                ""
            )
        )

    except ValueError:

        return

    set_banned(
        user_id,
        False
    )

    query.data = f"user_{user_id}"

    await admin_user_details(
        update,
        context
    )


# ============================================================
# روابط التحميل
# ============================================================

async def admin_user_links(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    parts = query.data.split("_")

    try:

        target_id = int(parts[1])
        page = int(parts[2])

    except (ValueError, IndexError):

        return

    per_page = 5
    offset = page * per_page

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            url,
            website,
            media_type,
            quality,
            created_at
        FROM downloads
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """, (
        target_id,
        per_page,
        offset,
    ))

    downloads = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) AS count
        FROM downloads
        WHERE user_id = ?
    """, (target_id,))

    total = cur.fetchone()["count"]

    conn.close()

    if not downloads:

        text = (
            "🔗 لا توجد تحميلات لهذا المستخدم."
        )

    else:

        text = (
            "🔗 روابط تحميل المستخدم\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        for index, row in enumerate(
            downloads,
            start=offset + 1
        ):

            media_type = (
                "🎥 فيديو"
                if row["media_type"] == "video"
                else "🎵 صوت"
            )

            text += (

                f"{index}. 🌐 {row['website']}\n"

                f"   📦 {media_type}\n"

                f"   🎚 {row['quality']}\n"

                f"   📅 {row['created_at']}\n"

                f"   🔗 {row['url']}\n\n"
            )

    keyboard = []

    navigation = []

    if page > 0:

        navigation.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"links_{target_id}_{page - 1}"
                )
            )
        )

    if offset + per_page < total:

        navigation.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"links_{target_id}_{page + 1}"
                )
            )
        )

    if navigation:

        keyboard.append(
            navigation
        )

    keyboard.append([

        InlineKeyboardButton(
            "🔙 المستخدم",
            callback_data=(
                f"user_{target_id}"
            )
        )

    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# ============================================================
# حذف المستخدم
# ============================================================

async def delete_user_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:

        user_id = int(
            query.data.replace(
                "delete_user_",
                ""
            )
        )

    except ValueError:

        return

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "⚠️ نعم، احذف",
                callback_data=(
                    f"confirm_delete_{user_id}"
                )
            )
        ],

        [
            InlineKeyboardButton(
                "🔙 إلغاء",
                callback_data=(
                    f"user_{user_id}"
                )
            )
        ],

    ])

    await query.edit_message_text(

        "⚠️ تأكيد حذف المستخدم\n\n"
        "سيتم حذف:\n"
        "• معلومات المستخدم\n"
        "• رقم الهاتف المحفوظ\n"
        "• الموقع المحفوظ\n"
        "• سجل التحميلات\n\n"
        "❗ لا يمكن التراجع عن هذه العملية.",

        reply_markup=keyboard
    )


async def confirm_delete_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:

        user_id = int(
            query.data.replace(
                "confirm_delete_",
                ""
            )
        )

    except ValueError:

        return

    delete_user(user_id)

    await query.edit_message_text(

        "✅ تم حذف المستخدم وبياناته بنجاح.",

        reply_markup=InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "👥 المستخدمون",
                    callback_data="admin_users_0"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔙 لوحة الإدارة",
                    callback_data="admin_home"
                )
            ],

        ])
    )


# ============================================================
# الإعلان
# ============================================================

async def broadcast_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if user.id != ADMIN_ID:
        return

    context.user_data[
        "waiting_broadcast"
    ] = True

    await update.message.reply_text(

        "📢 إرسال إعلان\n\n"
        "أرسل الآن نص الإعلان الذي تريد "
        "إرساله لجميع مستخدمي البوت.\n\n"
        "❌ للإلغاء أرسل:\n"
        "/cancel"
    )


async def admin_broadcast_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    context.user_data[
        "waiting_broadcast"
    ] = True

    await query.edit_message_text(

        "📢 إرسال إعلان\n\n"
        "أرسل الآن نص الإعلان.\n\n"
        "سيتم إرساله إلى جميع المستخدمين "
        "غير المحظورين.\n\n"
        "❌ للإلغاء استخدم /cancel"
    )


async def process_broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    if not context.user_data.get(
        "waiting_broadcast"
    ):
        return

    message = update.message.text.strip()

    if not message:

        await update.message.reply_text(
            "❌ الإعلان فارغ."
        )

        return

    if len(message) > MAX_BROADCAST_LENGTH:

        await update.message.reply_text(
            f"❌ الحد الأقصى للإعلان "
            f"{MAX_BROADCAST_LENGTH} حرف."
        )

        return

    context.user_data[
        "waiting_broadcast"
    ] = False

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO broadcast_logs (admin_id, message, sent_count, failed_count, created_at)
        VALUES (?, ?, 0, 0, ?)
    """, (ADMIN_ID, message, datetime.now().isoformat()))
    broadcast_id = cur.lastrowid
    conn.commit()

    cur.execute("""
        SELECT user_id
        FROM users
        WHERE is_banned = 0
    """)

    users = cur.fetchall()

    conn.close()

    sent = 0
    failed = 0

    status_message = await update.message.reply_text(
        "📢 جاري إرسال الإعلان...\n\n"
        f"👥 المستهدفون: {len(users)}"
    )

    for row in users:

        try:

            sent_message = await context.bot.send_message(
                chat_id=row["user_id"],
                text=message,
            )

            # حفظ رقم الرسالة حتى يستطيع الأدمن حذف الإعلان لاحقاً
            conn_save = get_db()
            cur_save = conn_save.cursor()

            cur_save.execute("""
                INSERT INTO broadcast_messages (
                    broadcast_id,
                    user_id,
                    message_id,
                    created_at
                )
                VALUES (?, ?, ?, ?)
            """, (
                broadcast_id,
                row["user_id"],
                sent_message.message_id,
                datetime.now().isoformat(),
            ))

            conn_save.commit()
            conn_save.close()

            sent += 1

            await asyncio.sleep(0.05)

        except Exception as e:

            print(
                f"Broadcast error "
                f"{row['user_id']}: {e}"
            )

            failed += 1

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE broadcast_logs SET sent_count = ?, failed_count = ? WHERE id = ?
    """, (sent, failed, broadcast_id))

    conn.commit()
    conn.close()

    await status_message.edit_text(

        "✅ انتهى إرسال الإعلان.\n\n"

        f"📨 تم الإرسال: {sent}\n"
        f"❌ فشل الإرسال: {failed}\n"
        f"👥 الإجمالي: {len(users)}"
    )


# ============================================================
# مسح الإعلانات المرسلة
# ============================================================

async def delete_broadcasts_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, user_id, message_id
        FROM broadcast_messages
        ORDER BY id ASC
    """)

    messages = cur.fetchall()
    conn.close()

    if not messages:
        await query.edit_message_text(
            "🗑️ لا توجد إعلانات محفوظة للحذف.\n\n"
            "الإعلانات الجديدة التي سترسلها بعد تفعيل هذه الميزة "
            "سيتم حفظها ويمكن حذفها لاحقاً."
        )
        return

    deleted = 0
    failed = 0

    status_message = await query.edit_message_text(
        "🗑️ جاري حذف الإعلانات المرسلة...\n\n"
        f"📨 الرسائل المسجلة: {len(messages)}\n"
        "⏳ يرجى الانتظار..."
    )

    for row in messages:
        try:
            await context.bot.delete_message(
                chat_id=row["user_id"],
                message_id=row["message_id"],
            )

            deleted += 1

        except Exception as e:
            failed += 1

            print(
                f"Broadcast delete error "
                f"{row['user_id']} / "
                f"{row['message_id']}: {e}"
            )

        await asyncio.sleep(0.05)

    # حذف السجلات بعد انتهاء العملية
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM broadcast_messages
    """)

    conn.commit()
    conn.close()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 لوحة الإدارة",
                callback_data="admin_home"
            )
        ]
    ])

    await status_message.edit_text(
        "✅ تم الانتهاء من مسح الإعلانات.\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🗑️ تم حذفها: {deleted}\n"
        f"⚠️ تعذر حذفها: {failed}\n"
        f"📨 الإجمالي: {len(messages)}\n\n"
        "💡 الإعلانات الجديدة سيتم حفظها تلقائياً "
        "لتتمكن من حذفها لاحقاً.",
        reply_markup=keyboard
    )


# ============================================================
# رسالة لمستخدم محدد
# ============================================================

async def message_user_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:

        user_id = int(
            query.data.replace(
                "message_user_",
                ""
            )
        )

    except ValueError:

        return

    context.user_data[
        "message_target"
    ] = user_id

    context.user_data[
        "waiting_user_message"
    ] = True

    await query.edit_message_text(

        "📢 رسالة إلى مستخدم\n\n"
        f"🆔 ID: {user_id}\n\n"
        "أرسل الرسالة الآن.\n\n"
        "❌ للإلغاء استخدم /cancel"
    )


async def process_user_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    if not context.user_data.get(
        "waiting_user_message"
    ):
        return

    target_id = context.user_data.get(
        "message_target"
    )

    message = update.message.text

    context.user_data[
        "waiting_user_message"
    ] = False

    try:

        await context.bot.send_message(
            chat_id=target_id,
            text=message,
        )

        await update.message.reply_text(
            "✅ تم إرسال الرسالة بنجاح."
        )

    except Exception as e:

        print(e)

        await update.message.reply_text(
            "❌ تعذر إرسال الرسالة."
        )


# ============================================================
# البحث
# ============================================================

async def admin_search_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    context.user_data[
        "waiting_admin_search"
    ] = True

    await query.edit_message_text(

        "🔍 البحث عن مستخدم\n\n"
        "أرسل:\n"
        "• Telegram ID\n"
        "• أو username\n"
        "• أو الاسم\n\n"
        "مثال:\n"
        "1486412391\n"
        "@username"
    )


async def process_admin_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    if not context.user_data.get(
        "waiting_admin_search"
    ):
        return

    context.user_data[
        "waiting_admin_search"
    ] = False

    value = update.message.text.strip()

    conn = get_db()
    cur = conn.cursor()

    if value.startswith("@"):

        username = value[1:]

        cur.execute("""
            SELECT user_id
            FROM users
            WHERE username = ?
            LIMIT 1
        """, (username,))

    elif value.isdigit():

        cur.execute("""
            SELECT user_id
            FROM users
            WHERE user_id = ?
            LIMIT 1
        """, (int(value),))

    else:

        cur.execute("""
            SELECT user_id
            FROM users
            WHERE
                first_name LIKE ?
                OR last_name LIKE ?
            LIMIT 1
        """, (
            f"%{value}%",
            f"%{value}%",
        ))

    row = cur.fetchone()

    conn.close()

    if not row:

        await update.message.reply_text(
            "❌ لم يتم العثور على المستخدم."
        )

        return

    user_id = row["user_id"]

    await update.message.reply_text(

        "✅ تم العثور على المستخدم.",

        reply_markup=InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "👤 عرض المستخدم",
                    callback_data=f"user_{user_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    "👥 قائمة المستخدمين",
                    callback_data="admin_users_0"
                )
            ],

        ])
    )


# ============================================================
# لوحة الإدارة الرئيسية
# ============================================================

# ============================================================
# 🧹 إدارة ذاكرة التخزين المؤقت
# ============================================================

async def admin_storage_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ نعم، نظّف الآن",
                callback_data="admin_storage_confirm"
            ),
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="admin_storage_cancel"
            ),
        ]
    ])

    await query.edit_message_text(
        "🧹 تفريغ ذاكرة التخزين\n\n"
        "سيتم حذف الملفات المؤقتة التي أنشأها البوت فقط.\n\n"
        "🗑️ سيتم تنظيف ملفات التحميل والضغط المؤقتة.\n"
        "🔒 قاعدة البيانات وملفات البوت لن تتأثر.\n\n"
        "هل تريد المتابعة؟",
        reply_markup=keyboard
    )


async def admin_storage_confirm_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    await query.edit_message_text(
        "⏳ جاري تنظيف ذاكرة التخزين...\n\n"
        "🧹 يتم الآن حذف الملفات المؤقتة، يرجى الانتظار."
    )

    tmp_dir = Path("/tmp")

    deleted_files = 0
    deleted_dirs = 0
    freed_bytes = 0

    try:
        if tmp_dir.exists():
            for item in tmp_dir.iterdir():

                if not item.is_dir():
                    continue

                if not item.name.startswith("videobot_"):
                    continue

                try:
                    for file_path in item.rglob("*"):
                        try:
                            if file_path.is_file():
                                freed_bytes += file_path.stat().st_size
                                deleted_files += 1
                        except Exception:
                            pass

                    shutil.rmtree(
                        item,
                        ignore_errors=True
                    )

                    deleted_dirs += 1

                except Exception as e:
                    print("⚠️ Storage cleanup error:")
                    print(repr(e))

        freed_mb = freed_bytes / 1024 / 1024
        freed_gb = freed_bytes / 1024 / 1024 / 1024

        if freed_gb >= 1:
            size_text = f"{freed_gb:.2f} GB"
        else:
            size_text = f"{freed_mb:.2f} MB"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 تنظيف مرة أخرى",
                    callback_data="admin_storage"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 لوحة الإدارة",
                    callback_data="admin_home"
                )
            ]
        ])

        await query.edit_message_text(
            "✅ تم تنظيف ذاكرة التخزين بنجاح!\n\n"
            f"🗑️ الملفات المحذوفة: {deleted_files}\n"
            f"📁 مجلدات العمليات المحذوفة: {deleted_dirs}\n"
            f"💾 المساحة المحررة: {size_text}\n\n"
            "🔒 قاعدة البيانات وملفات البوت لم تتأثر.",
            reply_markup=keyboard
        )

        print()
        print("===== ADMIN STORAGE CLEANUP =====")
        print(f"Deleted files: {deleted_files}")
        print(f"Deleted directories: {deleted_dirs}")
        print(f"Freed space: {size_text}")
        print("=================================")
        print()

    except Exception as e:

        print()
        print("===== STORAGE CLEANUP ERROR =====")
        print(repr(e))
        print("=================================")
        print()

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 المحاولة مرة أخرى",
                    callback_data="admin_storage"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 لوحة الإدارة",
                    callback_data="admin_home"
                )
            ]
        ])

        await query.edit_message_text(
            "❌ حدث خطأ أثناء تنظيف ذاكرة التخزين.\n\n"
            "يمكنك المحاولة مرة أخرى.",
            reply_markup=keyboard
        )


async def admin_storage_cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer("تم إلغاء العملية.")

    if update.effective_user.id != ADMIN_ID:
        return

    await query.edit_message_text(
        "🛠️ لوحة إدارة بوت التحميل\n\n"
        "اختر القسم الذي تريد إدارته:",
        reply_markup=admin_keyboard()
    )


async def admin_home_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    await query.edit_message_text(

        "🛠️ لوحة إدارة الحسيان\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "📊 لوحة المعلومات والتحليلات\n"
        "👥 إدارة المستخدمين\n"
        "📥 سجل التحميلات\n"
        "📢 الإعلانات\n"
        "💾 التخزين والنظام\n\n"

        "اختر القسم الذي تريد إدارته:",

        reply_markup=admin_keyboard()
    )


async def admin_stats_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    await show_admin_stats(query)


# ============================================================
# أمر الإحصائيات
# ============================================================

async def admin_stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ هذا الأمر متاح لمالك البوت فقط."
        )

        return

    data = get_statistics()

    text = (

        "📊 إحصائيات بوت التحميل\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 المستخدمون: {data['users']}\n"

        f"🚫 المحظورون: {data['banned']}\n"

        f"📥 التحميلات: {data['downloads']}\n"

        f"🎥 الفيديوهات: {data['videos']}\n"

        f"🎵 الصوتيات: {data['audio']}\n"

        f"📱 أرقام الهواتف: {data['phones']}\n"

        f"📍 المواقع: {data['locations']}\n\n"

        "🚻 الجنس:\n"
    )

    for row in data["genders"]:

        text += (
            f"• {row['gender']}: "
            f"{row['count']}\n"
        )

    await update.message.reply_text(
        text,
        reply_markup=admin_keyboard()
    )


# ============================================================
# أمر تحديد الجنس
# ============================================================

async def set_gender_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if user.id != ADMIN_ID:
        return

    if len(context.args) < 2:

        await update.message.reply_text(

            "الاستخدام:\n\n"
            "/setgender USER_ID male\n"
            "/setgender USER_ID female"
        )

        return

    try:

        target_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ ID غير صحيح."
        )

        return

    gender_value = context.args[1].lower()

    if gender_value in (
        "male",
        "ذكر"
    ):

        gender = "ذكر"

    elif gender_value in (
        "female",
        "أنثى",
        "انثى"
    ):

        gender = "أنثى"

    else:

        await update.message.reply_text(
            "❌ استخدم male أو female."
        )

        return

    save_gender(
        target_id,
        gender
    )

    await update.message.reply_text(
        "✅ تم حفظ جنس المستخدم."
    )


# ============================================================
# إلغاء
# ============================================================

async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    # A deferred compression owns a temporary directory until the user decides.
    temp_dir = context.user_data.get("compression_temp_dir")
    context.user_data.clear()
    if temp_dir and os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)

    await update.message.reply_text("✅ تم إلغاء العملية.")


# ============================================================
# الهاتف
# ============================================================

async def handle_contact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    if is_banned(user.id):

        await update.message.reply_text(
            TEXTS["ar"]["banned"]
        )

        return

    contact = update.message.contact

    if contact:
        if contact.user_id is not None and contact.user_id != user.id:
            await update.message.reply_text("❌ لا يمكن حفظ رقم جهة اتصال لشخص آخر.")
            return
        save_phone(user.id, contact.phone_number)

        language = get_language(
            user.id
        ) or "ar"

        await update.message.reply_text(
            "✅ تم حفظ رقم الهاتف بنجاح.\n\n"
            + TEXTS[language]["welcome"]
        )


# ============================================================
# الموقع
# ============================================================

async def handle_location(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    if is_banned(user.id):

        await update.message.reply_text(
            TEXTS["ar"]["banned"]
        )

        return

    location = update.message.location

    if location:

        save_location(
            user.id,
            location.latitude,
            location.longitude
        )

        language = get_language(
            user.id
        ) or "ar"

        await update.message.reply_text(
            "📍 تم حفظ موقعك بنجاح.\n\n"
            + TEXTS[language]["welcome"]
        )


# ============================================================
# راوتر رسائل المدير
#
# مهم جداً:
# إذا كان المدير لا ينتظر إعلاناً أو رسالة أو بحثاً،
# نمرر الرسالة إلى handle_message.
#
# هذا هو الإصلاح الأساسي لمشكلة عدم ظهور أزرار التحميل
# عند إرسال الرابط من حساب المدير.
# ============================================================

async def admin_text_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    if context.user_data.get(
        "waiting_broadcast"
    ):

        await process_broadcast(
            update,
            context
        )

        return

    if context.user_data.get(
        "waiting_user_message"
    ):

        await process_user_message(
            update,
            context
        )

        return

    if context.user_data.get(
        "waiting_admin_search"
    ):

        await process_admin_search(
            update,
            context
        )

        return

    # --------------------------------------------------------
    # إذا لم تكن هناك عملية إدارية قيد الانتظار،
    # تعامل مع الرسالة كرسالة مستخدم عادية.
    # --------------------------------------------------------

    await handle_message(
        update,
        context
    )


# ============================================================
# معالج الأخطاء
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print()
    print("====================================")
    print("❌ UNHANDLED BOT ERROR")
    print("====================================")

    print(
        repr(context.error)
    )

    print(
        "===================================="
    )
    print()


# ============================================================
# تشغيل البوت
# ============================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN غير موجود.\n"
            "قم بتعيينه قبل تشغيل البوت."
        )

    # --------------------------------------------------------
    # إنشاء / تحديث قاعدة البيانات
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # إعداد الاتصال مع Telegram
    # --------------------------------------------------------

    request = HTTPXRequest(

        connection_pool_size=30,

        connect_timeout=60,

        read_timeout=900,

        write_timeout=900,

        pool_timeout=60,

        http_version="1.1",
    )

    app = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .build()
    )

    # ========================================================
    # معالج الأخطاء
    # ========================================================

    app.add_error_handler(
        error_handler
    )

    # ========================================================
    # الأوامر
    # ========================================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "language",
            language_command
        )
    )

    app.add_handler(
        CommandHandler(
            "hebaali",
            admin_command
        )
    )

    app.add_handler(
        CommandHandler(
            "stats",
            admin_stats_command
        )
    )

    app.add_handler(
        CommandHandler(
            "broadcast",
            broadcast_command
        )
    )

    app.add_handler(
        CommandHandler(
            "cancel",
            cancel_command
        )
    )

    app.add_handler(
        CommandHandler(
            "setgender",
            set_gender_command
        )
    )

    # ========================================================
    # اللغة
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            language_callback,
            pattern=r"^language_"
        )
    )

    # ========================================================
    # الإدارة
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            admin_home_callback,
            pattern=r"^admin_home$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_stats_callback,
            pattern=r"^admin_stats$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_users,
            pattern=r"^admin_users_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_user_details,
            pattern=r"^user_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_user_links,
            pattern=r"^links_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            ban_user_callback,
            pattern=r"^ban_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            unban_user_callback,
            pattern=r"^unban_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_user_callback,
            pattern=r"^delete_user_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            confirm_delete_user,
            pattern=r"^confirm_delete_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_search_callback,
            pattern=r"^admin_search$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_broadcast_callback,
            pattern=r"^admin_broadcast$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_broadcasts_callback,
            pattern=r"^admin_delete_broadcasts$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_storage_callback,
            pattern=r"^admin_storage$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_storage_confirm_callback,
            pattern=r"^admin_storage_confirm$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_storage_cancel_callback,
            pattern=r"^admin_storage_cancel$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            message_user_callback,
            pattern=r"^message_user_"
        )
    )

    # ========================================================
    # لوحة الإدارة المتقدمة
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            admin_dashboard_callback,
            pattern=r"^admin_dashboard_(1|7|30)$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_recent_downloads_callback,
            pattern=r"^admin_recent_downloads$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_top_users_callback,
            pattern=r"^admin_top_users$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_top_websites_callback,
            pattern=r"^admin_top_websites$"
        )
    )

    # ========================================================
    # التحميل
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            download_media,
            pattern=r"^(video_|audio_|compress_|main_menu)"
        )
    )

    # ========================================================
    # الهاتف
    # ========================================================

    app.add_handler(
        MessageHandler(
            filters.CONTACT,
            handle_contact
        )
    )

    # ========================================================
    # الموقع
    # ========================================================

    app.add_handler(
        MessageHandler(
            filters.LOCATION,
            handle_location
        )
    )

    # ========================================================
    # رسائل المدير
    #
    # يجب أن تكون قبل معالج المستخدمين،
    # لأن المدير يحتاج إلى معالجة الإعلانات والبحث والرسائل.
    # ========================================================

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.User(ADMIN_ID),
            admin_text_router
        )
    )

    # ========================================================
    # رسائل باقي المستخدمين
    # ========================================================

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_message
        )
    )

    # ========================================================
    # التشغيل
    # ========================================================

    print()
    print("====================================")
    print("🤖 Download Bot is running...")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print("💎 Premium: DISABLED")
    print("⭐ Payments: DISABLED")
    print("🆓 All downloads are FREE")
    print("🗄️ Database migration: ENABLED")
    print("🔗 Admin link handling: FIXED")
    print("====================================")
    print()

    app.run_polling(
        drop_pending_updates=True
    )



# ============================================================
# لوحة الإدارة المتقدمة - الحسيان
# ============================================================

def get_admin_dashboard_data(days=30):

    conn = get_db()
    cur = conn.cursor()

    now = datetime.now()
    start = now.timestamp() - (days * 86400)

    # إجمالي المستخدمين
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM users
    """)
    total_users = cur.fetchone()["count"]

    # المستخدمون المحظورون
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM users
        WHERE is_banned = 1
    """)
    banned_users = cur.fetchone()["count"]

    # المستخدمون النشطون خلال الفترة
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM users
        WHERE last_seen IS NOT NULL
        AND last_seen >= ?
    """, (
        datetime.fromtimestamp(start).isoformat(),
    ))
    active_users = cur.fetchone()["count"]

    # إجمالي التحميلات خلال الفترة
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM downloads
        WHERE created_at >= ?
    """, (
        datetime.fromtimestamp(start).isoformat(),
    ))
    period_downloads = cur.fetchone()["count"]

    # الفيديو
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM downloads
        WHERE media_type = 'video'
        AND created_at >= ?
    """, (
        datetime.fromtimestamp(start).isoformat(),
    ))
    period_videos = cur.fetchone()["count"]

    # الصوت
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM downloads
        WHERE media_type = 'audio'
        AND created_at >= ?
    """, (
        datetime.fromtimestamp(start).isoformat(),
    ))
    period_audio = cur.fetchone()["count"]

    # أكثر المواقع
    cur.execute("""
        SELECT website, COUNT(*) AS count
        FROM downloads
        WHERE created_at >= ?
        GROUP BY website
        ORDER BY count DESC
        LIMIT 10
    """, (
        datetime.fromtimestamp(start).isoformat(),
    ))
    websites = cur.fetchall()

    # أكثر المستخدمين تحميلًا
    cur.execute("""
        SELECT
            d.user_id,
            d.username,
            COUNT(*) AS count
        FROM downloads d
        WHERE d.created_at >= ?
        GROUP BY d.user_id, d.username
        ORDER BY count DESC
        LIMIT 10
    """, (
        datetime.fromtimestamp(start).isoformat(),
    ))
    top_users = cur.fetchall()

    # آخر التحميلات
    cur.execute("""
        SELECT
            id,
            user_id,
            username,
            website,
            media_type,
            quality,
            created_at
        FROM downloads
        ORDER BY id DESC
        LIMIT 10
    """)
    recent_downloads = cur.fetchall()

    # اللغات
    cur.execute("""
        SELECT language, COUNT(*) AS count
        FROM users
        GROUP BY language
        ORDER BY count DESC
    """)
    languages = cur.fetchall()

    # الحجم التقريبي لقاعدة البيانات
    try:
        db_size = Path(DB_FILE).stat().st_size
    except Exception:
        db_size = 0

    conn.close()

    return {
        "total_users": total_users,
        "banned_users": banned_users,
        "active_users": active_users,
        "period_downloads": period_downloads,
        "period_videos": period_videos,
        "period_audio": period_audio,
        "websites": websites,
        "top_users": top_users,
        "recent_downloads": recent_downloads,
        "languages": languages,
        "db_size": db_size,
        "days": days,
    }


def format_bytes_admin(size):

    try:
        size = float(size)
    except Exception:
        return "0 B"

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
    ]

    for unit in units:

        if size < 1024:
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size:.2f} PB"


def admin_dashboard_text(data):

    days = data["days"]

    period_name = {
        1: "اليوم",
        7: "آخر 7 أيام",
        30: "آخر 30 يومًا",
    }.get(
        days,
        f"آخر {days} يوم"
    )

    text = (
        "📊 لوحة معلومات الحسيان\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 إجمالي المستخدمين: "
        f"{data['total_users']}\n"

        f"🟢 النشطون ({period_name}): "
        f"{data['active_users']}\n"

        f"🚫 المحظورون: "
        f"{data['banned_users']}\n\n"

        f"📥 التحميلات ({period_name}): "
        f"{data['period_downloads']}\n"

        f"🎥 الفيديوهات: "
        f"{data['period_videos']}\n"

        f"🎵 الصوتيات: "
        f"{data['period_audio']}\n\n"

        f"💾 حجم قاعدة البيانات: "
        f"{format_bytes_admin(data['db_size'])}\n\n"
    )

    text += "🌐 أكثر المنصات:\n"

    if data["websites"]:

        for row in data["websites"][:5]:

            website = (
                row["website"]
                or "غير معروف"
            )

            text += (
                f"• {html.escape(str(website))}: "
                f"{row['count']}\n"
            )

    else:

        text += "• لا توجد بيانات\n"

    text += "\n🏆 أكثر المستخدمين تحميلًا:\n"

    if data["top_users"]:

        for index, row in enumerate(
            data["top_users"][:5],
            1
        ):

            username = (
                f"@{row['username']}"
                if row["username"]
                else f"ID {row['user_id']}"
            )

            text += (
                f"{index}. "
                f"{html.escape(str(username))} "
                f"— {row['count']} تحميل\n"
            )

    else:

        text += "• لا توجد بيانات\n"

    return text


def admin_dashboard_keyboard(days=30):

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📅 اليوم",
                callback_data="admin_dashboard_1"
            ),
            InlineKeyboardButton(
                "📅 7 أيام",
                callback_data="admin_dashboard_7"
            ),
            InlineKeyboardButton(
                "📅 30 يوم",
                callback_data="admin_dashboard_30"
            ),
        ],

        [
            InlineKeyboardButton(
                "📥 سجل التحميلات",
                callback_data="admin_recent_downloads"
            )
        ],

        [
            InlineKeyboardButton(
                "🏆 أكثر المستخدمين",
                callback_data="admin_top_users"
            ),
            InlineKeyboardButton(
                "🌐 المنصات",
                callback_data="admin_top_websites"
            ),
        ],

        [
            InlineKeyboardButton(
                "💾 التخزين",
                callback_data="admin_storage"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 المستخدمون",
                callback_data="admin_users_0"
            ),
            InlineKeyboardButton(
                "📢 الإعلانات",
                callback_data="admin_broadcast"
            ),
        ],

        [
            InlineKeyboardButton(
                "🔄 تحديث",
                callback_data=f"admin_dashboard_{days}"
            ),
            InlineKeyboardButton(
                "🔙 الرئيسية",
                callback_data="admin_home"
            ),
        ],

    ])


async def admin_dashboard_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:

        days = int(
            query.data.replace(
                "admin_dashboard_",
                ""
            )
        )

    except ValueError:

        days = 30

    if days not in (1, 7, 30):
        days = 30

    data = get_admin_dashboard_data(days)

    await query.edit_message_text(
        admin_dashboard_text(data),
        reply_markup=admin_dashboard_keyboard(days)
    )


async def admin_recent_downloads_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            user_id,
            username,
            website,
            media_type,
            quality,
            created_at
        FROM downloads
        ORDER BY id DESC
        LIMIT 20
    """)

    rows = cur.fetchall()

    conn.close()

    text = (
        "📥 آخر التحميلات\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not rows:

        text += "لا توجد تحميلات حتى الآن."

    else:

        for row in rows:

            username = (
                f"@{row['username']}"
                if row["username"]
                else f"ID {row['user_id']}"
            )

            media = (
                "🎥"
                if row["media_type"] == "video"
                else "🎵"
            )

            website = (
                row["website"]
                or "غير معروف"
            )

            quality = (
                row["quality"]
                or "-"
            )

            created = (
                row["created_at"]
                or "-"
            )

            text += (
                f"{media} "
                f"{html.escape(str(username))}\n"
                f"🌐 {html.escape(str(website))}\n"
                f"⚙️ {html.escape(str(quality))}\n"
                f"🕐 {html.escape(str(created))}\n"
                "──────────────\n"
            )

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📊 لوحة المعلومات",
                callback_data="admin_dashboard_30"
            )
        ],

        [
            InlineKeyboardButton(
                "🔙 لوحة الإدارة",
                callback_data="admin_home"
            )
        ],

    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard
    )


async def admin_top_users_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    data = get_admin_dashboard_data(30)

    text = (
        "🏆 أكثر المستخدمين تحميلًا\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not data["top_users"]:

        text += "لا توجد بيانات."

    else:

        for index, row in enumerate(
            data["top_users"],
            1
        ):

            username = (
                f"@{row['username']}"
                if row["username"]
                else f"ID {row['user_id']}"
            )

            text += (
                f"{index}. "
                f"{html.escape(str(username))}\n"
                f"   📥 {row['count']} تحميل\n\n"
            )

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📊 لوحة المعلومات",
                callback_data="admin_dashboard_30"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 المستخدمون",
                callback_data="admin_users_0"
            )
        ],

        [
            InlineKeyboardButton(
                "🔙 لوحة الإدارة",
                callback_data="admin_home"
            )
        ],

    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard
    )


async def admin_top_websites_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    data = get_admin_dashboard_data(30)

    text = (
        "🌐 أكثر المنصات استخدامًا\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not data["websites"]:

        text += "لا توجد بيانات."

    else:

        for index, row in enumerate(
            data["websites"],
            1
        ):

            website = (
                row["website"]
                or "غير معروف"
            )

            text += (
                f"{index}. "
                f"{html.escape(str(website))}"
                f" — {row['count']} تحميل\n"
            )

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📊 لوحة المعلومات",
                callback_data="admin_dashboard_30"
            )
        ],

        [
            InlineKeyboardButton(
                "🔙 لوحة الإدارة",
                callback_data="admin_home"
            )
        ],

    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard
    )


# ============================================================
# بدء التشغيل
# ============================================================

if __name__ == "__main__":
    main()
