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
import json
import time
import uuid
from datetime import datetime
from urllib.parse import urlparse, urlunparse
from urllib.request import HTTPError, HTTPRedirectHandler, Request, build_opener, urlopen

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

try:
    from google import genai
except ImportError:
    genai = None


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
YOINKU_DOWNLOAD_TIMEOUT = 300
PROCESS_SHUTDOWN_TIMEOUT = 10
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_VIDEO_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_AUDIO_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_TELEGRAM_AUDIO_MB = 47
MAX_TELEGRAM_AUDIO_BYTES = (
    MAX_TELEGRAM_AUDIO_MB * 1024 * 1024
)
MAX_YOINKU_RESPONSE_BYTES = 1 * 1024 * 1024
MIN_FREE_SPACE_BYTES = 256 * 1024 * 1024
MAX_BROADCAST_LENGTH = 4000

logger = logging.getLogger(__name__)

# ============================================================
# Gemini AI
# ============================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = None

if GEMINI_API_KEY and genai is not None:
    try:
        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY
        )
        print("🤖 Gemini AI: ENABLED")
    except Exception as e:
        gemini_client = None
        logger.warning(
            "Gemini initialization failed: %s",
            type(e).__name__
        )
else:
    print("🤖 Gemini AI: DISABLED")


async def gemini_generate(prompt):
    """
    إرسال طلب إلى Gemini بدون تعطيل event loop الخاص بالبوت.
    مفتاح API لا يظهر في السجلات.
    """

    if gemini_client is None:
        raise RuntimeError(
            "Gemini AI is not configured"
        )

    def generate():
        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )

        return response.text or ""

    return await asyncio.to_thread(generate)


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
    """Resolve the final yt-dlp artifact safely from explicit output first."""
    base = os.path.realpath(temp_dir) + os.sep

    # Primary source: yt-dlp --print after_move:filepath
    for line in reversed(stdout_text.splitlines()):
        candidate = line.strip()
        if (
            candidate
            and candidate.lower().endswith(extensions)
            and os.path.realpath(candidate).startswith(base)
            and os.path.isfile(candidate)
        ):
            return os.path.realpath(candidate)

    # Defensive fallback: only inspect direct files created in our
    # trusted temporary directory. Never recurse outside temp_dir.
    try:
        candidates = []
        for name in os.listdir(temp_dir):
            candidate = os.path.join(temp_dir, name)
            real_candidate = os.path.realpath(candidate)

            if not real_candidate.startswith(base):
                continue
            if not os.path.isfile(real_candidate):
                continue
            if not name.startswith("download_"):
                continue
            if not name.lower().endswith(extensions):
                continue

            candidates.append(real_candidate)

        if candidates:
            candidates.sort(
                key=lambda p: os.path.getmtime(p),
                reverse=True,
            )
            return candidates[0]
    except OSError:
        pass

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
            "🎬 أهلاً وسهلاً بكم في بوت AliBot 🤍\n\n📥 بوتكم السهل والسريع لتحميل الفيديوهات والأصوات\nمن مختلف المنصات بجودة عالية وبكل سهولة.\n\n🔗 أرسل رابط الفيديو أو الصوت، ودع AliBot يتولى الباقي.\n\n🆓 تحميل مجاني • 🚀 سريع • 🎧 جودة عالية\n\n👨‍👩‍👧‍👦 لا تنسوا مشاركة البوت مع الأصدقاء والعائلة ❤️\n🔗 شارك الرابط ليستفيد الجميع 🌍\n\n👇 أرسل الرابط الآن وابدأ التحميل!"
            "🎬 حمّل فيديوهاتك وصوتياتك بسهولة وسرعة.\n"
            "⚡ جودة متعددة\n"
            "🎵 تحويل الفيديو إلى صوت\n"
            "🌍 دعم عدة منصات\n\n"
            "🌐 اختر لغة البوت:",

        "welcome":
            "🤖 ماذا يمكن لهذا البوت فعله؟\n\n"
            "🚀 AliBot - الأسرع لتحميل الفيديوهات\n\n"
            "حمّل مقاطعك المفضلة من تيك توك، إنستا، يوتيوب، وفيسبوك "
            "بجودة HD وبدون علامة مائية بضغطة زر.\n\n"
            "⚡ فورًا.\n\n"
            "✨ مميزات البوت\n\n"
            "📥 تحميل فوري: أرسل الرابط واستلم الفيديو.\n\n"
            "🛡️ بدون حقوق: احفظ المقاطع بنقائها الأصلي.\n"
            "🆓 مجاني وآمن: بدون إعلانات ولا حدود للتحميل.\n\n"
            "👥 شارك البوت مع أصدقائك عبر المعرف "
            "@MyVideoDownloaderAliBot لتعم الفائدة.\n\n"
            "👇 اضغط على زر ابدأ بالأسفل وانطلق!",

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
            "⏳ <b>AliBot | جاري تجهيز طلبك</b>\n\n"
            "👤 المستخدم: {username}\n"
            "🌐 المنصة: {website}\n"
            "🎚 الجودة: {quality}\n\n"
            "⚙️ جاري معالجة الرابط وتجهيز الملف...\n"
            "🚀 قد تستغرق العملية لحظات حسب حجم الفيديو وسرعة المنصة.\n\n"
            "💙 AliBot يعمل من أجلك، يرجى الانتظار...",

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
            "🙏 شكراً لاستخدامك <b>بوت AliBot</b>\n"
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
            "🙏 شكراً لاستخدامك <b>بوت AliBot</b>\n"
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
            "✨ Welcome to AliBot ✨\n\n"
            "🚀 Download your favorite videos quickly and easily from popular platforms.\n\n"
            "🎬 Multiple video qualities\n"
            "🎵 High-quality audio extraction\n"
            "⚡ Fast and reliable downloads\n"
            "🌐 Multiple platforms supported\n\n"
            "📎 Just send your video link and let AliBot handle the rest.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 AliBot\n"
            "💙 Fast • Simple • Free\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Enjoy your download! 🚀",

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
            "⏳ <b>AliBot | Preparing your request</b>\n\n"
            "👤 User: {username}\n"
            "🌐 Platform: {website}\n"
            "🎚 Quality: {quality}\n\n"
            "⚙️ Processing the link and preparing your file...\n"
            "🚀 This may take a moment depending on the file size and platform speed.\n\n"
            "💙 AliBot is working for you. Please wait...",

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
            "✨ AliBot'a hoş geldiniz ✨\n\n"
            "🚀 Favori videolarınızı popüler platformlardan hızlı ve kolay şekilde indirin.\n\n"
            "🎬 Birden fazla video kalitesi\n"
            "🎵 Yüksek kaliteli ses çıkarma\n"
            "⚡ Hızlı ve güvenilir indirme\n"
            "🌐 Birden fazla platform desteği\n\n"
            "📎 Video bağlantınızı gönderin, gerisini AliBot halletsin.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 AliBot\n"
            "💙 Hızlı • Basit • Ücretsiz\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "İyi indirmeler! 🚀",

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
            "⏳ <b>AliBot | İsteğiniz hazırlanıyor</b>\n\n"
            "👤 Kullanıcı: {username}\n"
            "🌐 Platform: {website}\n"
            "🎚 Kalite: {quality}\n\n"
            "⚙️ Bağlantı işleniyor ve dosyanız hazırlanıyor...\n"
            "🚀 Dosya boyutuna ve platform hızına bağlı olarak biraz sürebilir.\n\n"
            "💙 AliBot sizin için çalışıyor. Lütfen bekleyin...",

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
            "✨ Willkommen bei AliBot ✨\n\n"
            "🚀 Laden Sie Ihre Lieblingsvideos schnell und einfach von beliebten Plattformen herunter.\n\n"
            "🎬 Mehrere Videoqualitäten\n"
            "🎵 Hochwertige Audioextraktion\n"
            "⚡ Schnelle und zuverlässige Downloads\n"
            "🌐 Unterstützung mehrerer Plattformen\n\n"
            "📎 Senden Sie einfach Ihren Videolink und AliBot erledigt den Rest.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 AliBot\n"
            "💙 Schnell • Einfach • Kostenlos\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Viel Spaß beim Download! 🚀",

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
            "⏳ <b>AliBot | Anfrage wird vorbereitet</b>\n\n"
            "👤 Benutzer: {username}\n"
            "🌐 Plattform: {website}\n"
            "🎚 Qualität: {quality}\n\n"
            "⚙️ Link wird verarbeitet und Ihre Datei wird vorbereitet...\n"
            "🚀 Je nach Dateigröße und Plattformgeschwindigkeit kann dies einen Moment dauern.\n\n"
            "💙 AliBot arbeitet für Sie. Bitte warten...",

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


# ============================================================
# AI ERROR REPORTING
# ============================================================

def sanitize_error_for_storage(value, max_length=4000):
    """Remove secrets and sensitive URLs from diagnostic text."""
    if value is None:
        return ""

    text = str(value)

    # --------------------------------------------------------
    # Authorization: Bearer <secret>
    # --------------------------------------------------------

    text = re.sub(
        r"(Authorization\s*:\s*Bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"(Bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )

    # --------------------------------------------------------
    # KEY=value / KEY:value / KEY value
    # --------------------------------------------------------

    secret_keys = (
        "BOT_TOKEN",
        "GEMINI_API_KEY",
        "YOINKU_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "API_KEY",
        "AUTHORIZATION",
        "X-API-KEY",
        "X_API_KEY",
    )

    for key in secret_keys:
        text = re.sub(
            rf"({re.escape(key)}\s*[=:]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            rf"({re.escape(key)}\s+)[^\s,;]+",
            r"\1[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )

    # --------------------------------------------------------
    # Authorization header without Bearer
    # --------------------------------------------------------

    text = re.sub(
        r"(Authorization\s*:\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )

    # --------------------------------------------------------
    # JSON secrets
    # --------------------------------------------------------

    text = re.sub(
        r'("(?:api[_-]?key|token|access[_-]?token|secret|authorization)"\s*:\s*")[^"]*(")',
        r"\1[REDACTED]\2",
        text,
        flags=re.IGNORECASE,
    )

    # --------------------------------------------------------
    # Sensitive URL query parameters
    # --------------------------------------------------------

    text = re.sub(
        r"((?:[?&]|\b)(?:api[_-]?key|token|access[_-]?token|secret|key)\s*=)[^&\s]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )

    # --------------------------------------------------------
    # Existing URL redaction helper
    # --------------------------------------------------------

    try:
        text = redact_url(text)
    except Exception:
        pass

    return text[:max_length]


def _sanitize_error_value(value, max_length=4000):
    """Sanitize one diagnostic value before storing or sending to Gemini."""
    if value is None:
        return None

    if isinstance(value, (dict, list, tuple)):
        try:
            value = json.dumps(
                value,
                ensure_ascii=False,
                default=str,
            )
        except Exception:
            value = str(value)
    else:
        value = str(value)

    return sanitize_error_for_storage(
        value,
        max_length=max_length,
    )


def _sanitize_error_details(details):
    """Recursively sanitize diagnostic details."""
    if details is None:
        return {}

    if isinstance(details, dict):
        result = {}
        for key, value in details.items():
            safe_key = str(key)[:100]

            if isinstance(value, dict):
                result[safe_key] = _sanitize_error_details(value)

            elif isinstance(value, (list, tuple)):
                result[safe_key] = [
                    _sanitize_error_details(item)
                    if isinstance(item, dict)
                    else _sanitize_error_value(item, 2000)
                    for item in value[:50]
                ]

            else:
                result[safe_key] = _sanitize_error_value(
                    value,
                    4000,
                )

        return result

    if isinstance(details, (list, tuple)):
        return [
            _sanitize_error_details(item)
            if isinstance(item, dict)
            else _sanitize_error_value(item, 2000)
            for item in details[:50]
        ]

    return _sanitize_error_value(details, 4000)


def _details_to_json(details):
    """Convert sanitized diagnostics to bounded JSON."""
    if not details:
        return None

    try:
        safe_details = _sanitize_error_details(details)

        return json.dumps(
            safe_details,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )[:12000]

    except Exception:
        return None


def log_download_error(
    user=None,
    user_id=None,
    username=None,
    url=None,
    website=None,
    media_type=None,
    stage=None,
    error_type=None,
    error_message=None,
    traceback_text=None,
    yoinku_used=False,
    attempt_id=None,
    attempt_number=None,
    duration_ms=None,
    return_code=None,
    exception_type=None,
    http_status=None,
    response_type=None,
    bytes_downloaded=None,
    candidate_index=None,
    candidate_count=None,
    details=None,
):
    """Store a sanitized technical download error."""

    try:
        if user is not None:
            user_id = getattr(user, "id", user_id)
            username = getattr(user, "username", username)

        if not attempt_id:
            attempt_id = uuid.uuid4().hex[:16]

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO error_logs (
                user_id,
                username,
                url,
                website,
                media_type,
                stage,
                error_type,
                error_message,
                traceback,
                yoinku_used,
                attempt_id,
                attempt_number,
                duration_ms,
                return_code,
                exception_type,
                http_status,
                response_type,
                bytes_downloaded,
                candidate_index,
                candidate_count,
                details_json,
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                user_id,
                username,
                redact_url(url) if url else "",
                website or "",
                media_type or "",
                stage or "unknown",
                error_type or "UnknownError",

                _sanitize_error_value(
                    error_message,
                    4000,
                ),

                _sanitize_error_value(
                    traceback_text,
                    8000,
                ),

                1 if yoinku_used else 0,

                attempt_id,
                attempt_number,
                duration_ms,
                return_code,
                exception_type,
                http_status,
                _sanitize_error_value(
                    response_type,
                    300,
                ),
                bytes_downloaded,
                candidate_index,
                candidate_count,

                _details_to_json(details),

                datetime.now().isoformat(),
            ),
        )

        conn.commit()
        conn.close()

    except Exception as exc:
        logger.exception(
            "Failed to save download error: %s",
            type(exc).__name__,
        )


def get_ai_errors_data(days=30):
    """Return aggregated error information for Gemini.

    Error records are grouped by attempt_id when available so that
    multiple failed stages of one download are not counted as
    independent incidents.
    """
    conn = get_db()
    cur = conn.cursor()

    cutoff = datetime.fromtimestamp(
        datetime.now().timestamp() - days * 86400
    ).isoformat()

    # --------------------------------------------------------
    # إجمالي سجلات الأخطاء
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT COUNT(*) AS total
        FROM error_logs
        WHERE created_at >= ?
        """,
        (cutoff,),
    )
    total_error_records = cur.fetchone()["total"] or 0

    # --------------------------------------------------------
    # إجمالي محاولات التحميل الفعلية
    #
    # كل attempt_id يمثل محاولة تحميل واحدة.
    # السجلات القديمة التي لا تحتوي attempt_id تُحسب كسجلات
    # مستقلة حتى لا نفقد بياناتها.
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT
            COUNT(
                DISTINCT CASE
                    WHEN attempt_id IS NOT NULL
                         AND attempt_id != ''
                    THEN attempt_id
                END
            ) AS grouped_attempts,
            SUM(
                CASE
                    WHEN attempt_id IS NULL
                         OR attempt_id = ''
                    THEN 1
                    ELSE 0
                END
            ) AS legacy_records
        FROM error_logs
        WHERE created_at >= ?
        """,
        (cutoff,),
    )

    attempt_row = cur.fetchone()

    grouped_attempts = (
        attempt_row["grouped_attempts"] or 0
    )
    legacy_records = (
        attempt_row["legacy_records"] or 0
    )

    total_attempts = (
        grouped_attempts + legacy_records
    )

    # --------------------------------------------------------
    # المواقع / المنصات الأكثر تسببًا بالأخطاء
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT website, COUNT(*) AS count
        FROM error_logs
        WHERE created_at >= ?
        GROUP BY website
        ORDER BY count DESC
        LIMIT 10
        """,
        (cutoff,),
    )

    websites = [
        {
            "website": row["website"] or "unknown",
            "count": row["count"] or 0,
        }
        for row in cur.fetchall()
    ]

    # --------------------------------------------------------
    # المراحل الأكثر فشلًا
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT stage, COUNT(*) AS count
        FROM error_logs
        WHERE created_at >= ?
        GROUP BY stage
        ORDER BY count DESC
        LIMIT 10
        """,
        (cutoff,),
    )

    stages = [
        {
            "stage": row["stage"] or "unknown",
            "count": row["count"] or 0,
        }
        for row in cur.fetchall()
    ]

    # --------------------------------------------------------
    # أنواع الأخطاء الأكثر تكرارًا
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT error_type, COUNT(*) AS count
        FROM error_logs
        WHERE created_at >= ?
        GROUP BY error_type
        ORDER BY count DESC
        LIMIT 10
        """,
        (cutoff,),
    )

    error_types = [
        {
            "error_type": row["error_type"] or "UnknownError",
            "count": row["count"] or 0,
        }
        for row in cur.fetchall()
    ]

    # --------------------------------------------------------
    # المحاولات التي تحتوي على أكثر من سجل خطأ
    #
    # هذا هو الجزء المهم لتحليل Gemini:
    # محاولة واحدة قد تحتوي yt-dlp + fallback + yoinku
    # + download، لكنها تبقى حادثة واحدة.
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT
            attempt_id,
            website,
            media_type,
            MIN(created_at) AS started_at,
            MAX(created_at) AS last_error_at,
            COUNT(*) AS error_records,
            GROUP_CONCAT(
                DISTINCT stage
            ) AS failed_stages,
            GROUP_CONCAT(
                DISTINCT error_type
            ) AS error_types,
            MAX(yoinku_used) AS yoinku_used
        FROM error_logs
        WHERE created_at >= ?
          AND attempt_id IS NOT NULL
          AND attempt_id != ''
        GROUP BY attempt_id
        ORDER BY last_error_at DESC
        LIMIT 50
        """,
        (cutoff,),
    )

    attempt_summaries = []

    for row in cur.fetchall():
        failed_stages = [
            item.strip()
            for item in (
                row["failed_stages"] or ""
            ).split(",")
            if item.strip()
        ]

        attempt_error_types = [
            item.strip()
            for item in (
                row["error_types"] or ""
            ).split(",")
            if item.strip()
        ]

        attempt_summaries.append(
            {
                "attempt_id": row["attempt_id"],
                "website": row["website"] or "unknown",
                "media_type": row["media_type"] or "",
                "started_at": row["started_at"],
                "last_error_at": row["last_error_at"],
                "error_records": row["error_records"] or 0,
                "failed_stages": failed_stages,
                "error_types": attempt_error_types,
                "yoinku_used": bool(
                    row["yoinku_used"]
                ),
            }
        )

    attempts_with_multiple_errors = sum(
        1
        for attempt in attempt_summaries
        if attempt["error_records"] > 1
    )

    # --------------------------------------------------------
    # آخر سجلات الأخطاء للتفاصيل
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT
            id,
            attempt_id,
            attempt_number,
            website,
            media_type,
            stage,
            error_type,
            error_message,
            yoinku_used,
            duration_ms,
            return_code,
            exception_type,
            http_status,
            response_type,
            bytes_downloaded,
            candidate_index,
            candidate_count,
            created_at
        FROM error_logs
        WHERE created_at >= ?
        ORDER BY id DESC
        LIMIT 30
        """,
        (cutoff,),
    )

    recent_errors = []

    for row in cur.fetchall():
        recent_errors.append(
            {
                "id": row["id"],
                "attempt_id": row["attempt_id"] or "",
                "attempt_number": row["attempt_number"],
                "website": row["website"] or "",
                "media_type": row["media_type"] or "",
                "stage": row["stage"] or "",
                "error_type": row["error_type"] or "",
                "error_message": sanitize_error_for_storage(
                    row["error_message"],
                    1200,
                ),
                "yoinku_used": bool(
                    row["yoinku_used"]
                ),
                "duration_ms": row["duration_ms"],
                "return_code": row["return_code"],
                "exception_type": row["exception_type"] or "",
                "http_status": row["http_status"],
                "response_type": row["response_type"] or "",
                "bytes_downloaded": row["bytes_downloaded"],
                "candidate_index": row["candidate_index"],
                "candidate_count": row["candidate_count"],
                "created_at": row["created_at"],
            }
        )

    conn.close()

    return {
        "period_days": days,

        # عدد السجلات وليس عدد الحوادث.
        "total_error_records": total_error_records,

        # عدد محاولات التحميل الفعلية قدر الإمكان.
        "total_attempts": total_attempts,

        # المحاولات التي نتج عنها أكثر من سجل خطأ.
        "attempts_with_multiple_errors": (
            attempts_with_multiple_errors
        ),

        "websites": websites,
        "stages": stages,
        "error_types": error_types,
        "attempt_summaries": attempt_summaries,
        "recent_errors": recent_errors,
    }


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
    # جدول أخطاء التحميل
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            url TEXT,
            website TEXT,
            media_type TEXT,
            stage TEXT,
            error_type TEXT,
            error_message TEXT,
            traceback TEXT,
            yoinku_used INTEGER DEFAULT 0,
            attempt_id TEXT,
            attempt_number INTEGER,
            duration_ms INTEGER,
            return_code INTEGER,
            exception_type TEXT,
            http_status INTEGER,
            response_type TEXT,
            bytes_downloaded INTEGER,
            candidate_index INTEGER,
            candidate_count INTEGER,
            details_json TEXT,
            created_at TEXT
        )
    """)

    # --------------------------------------------------------
    # Migration للأعمدة الجديدة في قواعد البيانات القديمة
    # --------------------------------------------------------

    error_log_columns = {
        "attempt_id": "TEXT",
        "attempt_number": "INTEGER",
        "duration_ms": "INTEGER",
        "return_code": "INTEGER",
        "exception_type": "TEXT",
        "http_status": "INTEGER",
        "response_type": "TEXT",
        "bytes_downloaded": "INTEGER",
        "candidate_index": "INTEGER",
        "candidate_count": "INTEGER",
        "details_json": "TEXT",
    }

    cur.execute("PRAGMA table_info(error_logs)")
    existing_error_columns = {
        row["name"]
        for row in cur.fetchall()
    }

    for column_name, column_type in error_log_columns.items():
        if column_name not in existing_error_columns:
            cur.execute(
                f"ALTER TABLE error_logs ADD COLUMN {column_name} {column_type}"
            )

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_error_logs_attempt_id
        ON error_logs(attempt_id)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_error_logs_created_at
        ON error_logs(created_at)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_error_logs_stage
        ON error_logs(stage)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_error_logs_website
        ON error_logs(website)
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
        TEXTS[language]["welcome"],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "▶️ ابدأ الآن",
                    callback_data="start_button"
                )
            ]
        ])
    )


# ============================================================
# تغيير اللغة
# ============================================================

async def start_button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    user = update.effective_user

    if is_banned(user.id):
        await query.message.reply_text(
            TEXTS["ar"]["banned"]
        )
        return

    language = get_language(user.id) or "ar"

    await query.message.reply_text(
        TEXTS[language]["send_link"]
    )


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

async def download_with_yoinku(
    url,
    temp_dir,
    is_audio=False,
    attempt_id=None,
    attempt_number=None,
):
    """Use Yoinku as a bounded fallback with sanitized diagnostics."""

    api_key = os.getenv("YOINKU_API_KEY")

    started_at = time.monotonic()

    diagnostics = {
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "status": "not_started",
        "duration_ms": None,
        "internal_attempts": 0,
        "exception_type": None,
        "error_message": None,
        "http_status": None,
        "response_type": None,
        "bytes_downloaded": None,
    }

    if not api_key:
        diagnostics.update({
            "status": "not_configured",
            "exception_type": "MissingAPIKey",
            "error_message": "YOINKU_API_KEY is not configured.",
            "duration_ms": int(
                (time.monotonic() - started_at) * 1000
            ),
        })

        logger.warning("YOINKU_API_KEY is not configured")
        return None, diagnostics

    import json
    import urllib.parse

    limit = (
        MAX_AUDIO_DOWNLOAD_BYTES
        if is_audio
        else MAX_VIDEO_DOWNLOAD_BYTES
    )

    if shutil.disk_usage(temp_dir).free < min(
        limit,
        MIN_FREE_SPACE_BYTES,
    ):
        diagnostics.update({
            "status": "insufficient_space",
            "exception_type": "InsufficientFreeSpace",
            "error_message": (
                "Insufficient free space for Yoinku download."
            ),
            "duration_ms": int(
                (time.monotonic() - started_at) * 1000
            ),
        })

        logger.warning(
            "Insufficient free space for Yoinku download"
        )
        return None, diagnostics

    api_url = (
        "https://yoinku.com/api/v1/download?"
        + urllib.parse.urlencode({
            "url": url,
            "format": "a-320" if is_audio else "v-720",
        })
    )

    output_file = os.path.join(
        temp_dir,
        "yoinku_download"
        + (".mp3" if is_audio else ".mp4"),
    )

    fetch_deadline = (
        time.monotonic()
        + YOINKU_DOWNLOAD_TIMEOUT
    )

    def fetch():

        request = Request(
            api_url,
            headers={
                "x-api-key": api_key,
                "Accept": "application/json",
                "User-Agent": "VideoBot/1.0",
            },
        )

        remaining_time = fetch_deadline - time.monotonic()

        if remaining_time <= 0:
            raise TimeoutError(
                "Yoinku download exceeded "
                f"{YOINKU_DOWNLOAD_TIMEOUT} seconds"
            )

        with safe_urlopen(
            request,
            timeout=min(30, max(1, remaining_time)),
            max_bytes=MAX_YOINKU_RESPONSE_BYTES,
            expected_content_types={"application/json"},
        ) as response:

            diagnostics["http_status"] = getattr(
                response,
                "status",
                None,
            )

            diagnostics["response_type"] = (
                response.headers.get("Content-Type")
                if getattr(response, "headers", None)
                else None
            )

            response_bytes = read_limited(
                response,
                MAX_YOINKU_RESPONSE_BYTES,
            )

            data = json.loads(
                response_bytes.decode("utf-8")
            )

        direct_url = (
            data.get("url")
            if isinstance(data, dict)
            and data.get("ok")
            else None
        )

        if not direct_url:
            raise ValueError(
                "Yoinku response did not contain a download URL"
            )

        validate_public_http_url(direct_url)

        request = Request(
            direct_url,
            headers={
                "User-Agent": "VideoBot/1.0"
            },
        )

        try:
            remaining_time = fetch_deadline - time.monotonic()

            if remaining_time <= 0:
                raise TimeoutError(
                    "Yoinku download exceeded "
                    f"{YOINKU_DOWNLOAD_TIMEOUT} seconds"
                )

            with safe_urlopen(
                request,
                timeout=min(60, max(1, remaining_time)),
                max_bytes=limit,
            ) as response, open(
                output_file,
                "wb",
            ) as output:

                diagnostics["http_status"] = getattr(
                    response,
                    "status",
                    None,
                )

                diagnostics["response_type"] = (
                    response.headers.get("Content-Type")
                    if getattr(response, "headers", None)
                    else diagnostics["response_type"]
                )

                total = 0

                while True:
                    remaining_time = (
                        fetch_deadline - time.monotonic()
                    )

                    if remaining_time <= 0:
                        raise TimeoutError(
                            "Yoinku download exceeded "
                            f"{YOINKU_DOWNLOAD_TIMEOUT} seconds"
                        )

                    chunk = response.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    total += len(chunk)

                    if total > limit:
                        raise ValueError(
                            "Yoinku file exceeds configured size limit"
                        )

                    output.write(chunk)

                diagnostics["bytes_downloaded"] = total

        except Exception:
            try:
                os.remove(output_file)
            except FileNotFoundError:
                pass

            raise

        if (
            not os.path.isfile(output_file)
            or os.path.getsize(output_file) == 0
        ):
            raise ValueError(
                "Yoinku returned an empty file"
            )

        return output_file

    for attempt in range(3):
        diagnostics["internal_attempts"] = attempt + 1

        try:
            result = await asyncio.to_thread(fetch)

            diagnostics.update({
                "status": "success",
                "duration_ms": int(
                    (time.monotonic() - started_at) * 1000
                ),
            })

            return result, diagnostics

        except HTTPError as exc:
            diagnostics["http_status"] = getattr(exc, "code", None)
            diagnostics["exception_type"] = type(exc).__name__

            retry_after = None
            try:
                retry_after = exc.headers.get("Retry-After")
            except Exception:
                retry_after = None

            diagnostics["retry_after"] = retry_after
            diagnostics["error_message"] = (
                sanitize_error_for_storage(str(exc))
            )

            logger.warning(
                "Yoinku HTTP failure on attempt %d: %s (HTTP status: %s, Retry-After: %s)",
                attempt + 1,
                type(exc).__name__,
                diagnostics.get("http_status"),
                retry_after,
            )

            if getattr(exc, "code", None) == 429:
                break

            if time.monotonic() >= fetch_deadline:
                break

            if attempt < 2:
                remaining_time = (
                    fetch_deadline - time.monotonic()
                )

                if remaining_time <= 0:
                    break

                await asyncio.sleep(
                    min(2 ** attempt, remaining_time)
                )

        except (OSError, TimeoutError) as exc:
            http_code = getattr(exc, "code", None)
            if http_code is not None:
                diagnostics["http_status"] = http_code
            diagnostics["exception_type"] = type(
                exc
            ).__name__

            diagnostics["error_message"] = (
                sanitize_error_for_storage(
                    str(exc)
                )
            )

            logger.warning(
                "Yoinku temporary failure on attempt %d: %s (HTTP status: %s)",
                attempt + 1,
                type(exc).__name__,
                diagnostics.get("http_status"),
            )

            if time.monotonic() >= fetch_deadline:
                break

            if attempt < 2:
                remaining_time = (
                    fetch_deadline - time.monotonic()
                )

                if remaining_time <= 0:
                    break

                await asyncio.sleep(
                    min(2 ** attempt, remaining_time)
                )

        except Exception as exc:
            diagnostics["exception_type"] = type(
                exc
            ).__name__

            diagnostics["error_message"] = (
                sanitize_error_for_storage(
                    str(exc)
                )
            )

            logger.warning(
                "Yoinku fallback failed: %s",
                type(exc).__name__,
            )

            break

    diagnostics.update({
        "status": "failed",
        "duration_ms": int(
            (time.monotonic() - started_at) * 1000
        ),
    })

    return None, diagnostics

# ============================================================
# حد حجم فيديو Telegram
# ============================================================

MAX_TELEGRAM_VIDEO_MB = 49
MAX_TELEGRAM_VIDEO_BYTES = MAX_TELEGRAM_VIDEO_MB * 1024 * 1024



async def download_with_fallback(
    url,
    temp_dir,
    output_template,
    format_option,
    is_audio=False,
    attempt_id=None,
    attempt_number=None,
):
    """
    Download direct media candidates with detailed diagnostics.

    Returns:
        (final_path, stdout_text, stderr_text, diagnostics)
    """

    if not attempt_id:
        attempt_id = uuid.uuid4().hex

    started_at = time.monotonic()

    diagnostics = {
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "candidate_count": 0,
        "candidates": [],
        "total_duration_ms": None,
    }

    try:
        candidates = await extract_direct_media_urls(url)
    except Exception as exc:
        diagnostics["total_duration_ms"] = int(
            (time.monotonic() - started_at) * 1000
        )

        diagnostics["extraction_error"] = {
            "exception_type": type(exc).__name__,
            "error_message": sanitize_error_for_storage(str(exc)),
        }

        logger.warning(
            "Direct media URL extraction failed: %s",
            type(exc).__name__,
        )

        return None, None, None, diagnostics

    if not candidates:
        diagnostics["total_duration_ms"] = int(
            (time.monotonic() - started_at) * 1000
        )

        diagnostics["extraction_error"] = {
            "exception_type": "NoCandidates",
            "error_message": "No direct media candidates were extracted.",
        }

        logger.warning(
            "Direct fallback produced no candidates for %s",
            redact_url(url),
        )

        return None, None, None, diagnostics

    extensions = (
        (".mp3", ".m4a", ".opus", ".aac", ".wav")
        if is_audio
        else
        (".mp4", ".mkv", ".webm", ".mov")
    )

    max_size = (
        MAX_AUDIO_DOWNLOAD_BYTES
        if is_audio
        else MAX_VIDEO_DOWNLOAD_BYTES
    )

    diagnostics["candidate_count"] = len(candidates)

    last_stdout = None
    last_stderr = None

    for candidate_index, direct_url in enumerate(
        candidates,
        start=1,
    ):
        candidate_started_at = time.monotonic()

        candidate_info = {
            "candidate_index": candidate_index,
            "candidate_count": len(candidates),
            "status": "started",
            "duration_ms": None,
            "return_code": None,
            "exception_type": None,
            "error_message": None,
            "bytes_downloaded": None,
            "response_type": None,
        }

        diagnostics["candidates"].append(candidate_info)

        command = [
            "python",
            "-m",
            "yt_dlp",
            "--no-playlist",
            "-f",
            format_option,
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--socket-timeout",
            "60",
            "--concurrent-fragments",
            "2",
            "--max-filesize",
            str(max_size),
            "--print",
            "after_move:filepath",
            "--no-warnings",
            "-o",
            os.path.join(
                temp_dir,
                "fallback_%(id)s.%(ext)s",
            ),
        ]

        if is_audio:
            command.extend(
                [
                    "-x",
                    "--audio-format",
                    "mp3",
                ]
            )
        else:
            command.extend(
                [
                    "--merge-output-format",
                    "mp4",
                ]
            )

        command.append(direct_url)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await communicate_with_cleanup(
                process,
                DOWNLOAD_TIMEOUT,
            )

            stdout_text = stdout.decode(
                errors="ignore"
            )
            stderr_text = stderr.decode(
                errors="ignore"
            )

            last_stdout = stdout_text
            last_stderr = stderr_text

            candidate_info["return_code"] = process.returncode

            if process.returncode == 0:
                final_path = final_output_from_yt_dlp(
                    stdout_text,
                    temp_dir,
                    extensions,
                )

                if final_path:
                    try:
                        candidate_info["bytes_downloaded"] = (
                            os.path.getsize(final_path)
                        )
                    except OSError:
                        candidate_info["bytes_downloaded"] = None

                    candidate_info["status"] = "success"

                    candidate_info["duration_ms"] = int(
                        (time.monotonic() - candidate_started_at)
                        * 1000
                    )

                    diagnostics["total_duration_ms"] = int(
                        (time.monotonic() - started_at)
                        * 1000
                    )

                    return (
                        final_path,
                        stdout_text,
                        stderr_text,
                        diagnostics,
                    )

                candidate_info["status"] = "no_output_file"
                candidate_info["error_message"] = (
                    "yt-dlp exited successfully but no usable output file was found."
                )

            else:
                candidate_info["status"] = "failed"
                candidate_info["error_message"] = (
                    sanitize_error_for_storage(
                        stderr_text[-4000:]
                        or stdout_text[-4000:]
                        or (
                            "yt-dlp exited with code "
                            f"{process.returncode}"
                        )
                    )
                )

            candidate_info["duration_ms"] = int(
                (time.monotonic() - candidate_started_at)
                * 1000
            )

            logger.warning(
                "Direct fallback candidate %d/%d failed for %s "
                "(return_code=%s, duration_ms=%s)",
                candidate_index,
                len(candidates),
                redact_url(direct_url),
                process.returncode,
                candidate_info["duration_ms"],
            )

        except asyncio.TimeoutError:
            candidate_info["status"] = "timeout"
            candidate_info["exception_type"] = "TimeoutError"
            candidate_info["error_message"] = (
                "Direct fallback candidate timed out."
            )
            candidate_info["duration_ms"] = int(
                (time.monotonic() - candidate_started_at)
                * 1000
            )

            logger.warning(
                "Direct fallback candidate %d/%d timed out for %s",
                candidate_index,
                len(candidates),
                redact_url(direct_url),
            )

            return None, last_stdout, last_stderr, diagnostics

        except asyncio.CancelledError:
            candidate_info["status"] = "cancelled"
            candidate_info["exception_type"] = "CancelledError"
            candidate_info["error_message"] = (
                "Direct fallback candidate was cancelled."
            )
            candidate_info["duration_ms"] = int(
                (time.monotonic() - candidate_started_at)
                * 1000
            )
            raise

        except Exception as exc:
            candidate_info["status"] = "exception"
            candidate_info["exception_type"] = type(exc).__name__
            candidate_info["error_message"] = (
                sanitize_error_for_storage(
                    str(exc)
                )
            )

            candidate_info["duration_ms"] = int(
                (time.monotonic() - candidate_started_at)
                * 1000
            )

            logger.warning(
                "Direct fallback candidate %d/%d error: %s",
                candidate_index,
                len(candidates),
                type(exc).__name__,
            )

    diagnostics["total_duration_ms"] = int(
        (time.monotonic() - started_at) * 1000
    )

    return (
        None,
        last_stdout,
        last_stderr,
        diagnostics,
    )


# ============================================================
# التحميل
# ============================================================



async def split_video_for_telegram(
    media_file,
    output_dir,
    max_part_bytes,
):
    """
    Split a large video into Telegram-safe parts without re-encoding.

    FFmpeg uses stream copy (-c copy), so the original video/audio
    quality is preserved. The initial number of parts is estimated
    from the original file size, then oversized parts are split again.
    """

    os.makedirs(output_dir, exist_ok=True)

    original_size = os.path.getsize(media_file)

    initial_parts = max(
        2,
        (original_size + max_part_bytes - 1) // max_part_bytes,
    )

    probe_process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        media_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    probe_stdout, probe_stderr = await probe_process.communicate()

    if probe_process.returncode != 0:
        raise RuntimeError(
            "ffprobe failed: "
            + probe_stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
        )

    try:
        duration = float(
            probe_stdout.decode(
                "utf-8",
                errors="replace",
            ).strip()
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "Could not determine video duration"
        ) from exc

    if duration <= 0:
        raise RuntimeError("Invalid video duration")

    async def create_part(
        source_file,
        start_time,
        part_duration,
        output_file,
    ):
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_time:.3f}",
            "-i",
            source_file,
            "-t",
            f"{part_duration:.3f}",
            "-map",
            "0",
            "-c",
            "copy",
            "-reset_timestamps",
            "1",
            output_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(
                "FFmpeg split failed: "
                + stderr.decode(
                    "utf-8",
                    errors="replace",
                ).strip()
            )

        if not os.path.isfile(output_file):
            raise RuntimeError(
                f"FFmpeg did not create: {output_file}"
            )

    # Initial split.
    initial_duration = duration / initial_parts
    initial_files = []

    for index in range(initial_parts):
        start_time = initial_duration * index

        if index == initial_parts - 1:
            part_duration = max(
                0.1,
                duration - start_time,
            )
        else:
            part_duration = initial_duration

        output_file = os.path.join(
            output_dir,
            f"split_{index + 1:04d}.mp4",
        )

        await create_part(
            media_file,
            start_time,
            part_duration,
            output_file,
        )

        initial_files.append(output_file)

    # Recursively split any part that is still too large.
    final_files = []

    async def process_part(source_file, part_number):
        source_size = os.path.getsize(source_file)

        if source_size <= max_part_bytes:
            final_files.append(source_file)
            return

        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            source_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        probe_out, probe_err = await probe.communicate()

        if probe.returncode != 0:
            raise RuntimeError(
                "ffprobe failed while checking split part: "
                + probe_err.decode(
                    "utf-8",
                    errors="replace",
                ).strip()
            )

        try:
            part_duration = float(
                probe_out.decode(
                    "utf-8",
                    errors="replace",
                ).strip()
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Could not determine duration of {source_file}"
            ) from exc

        if part_duration <= 1.0:
            raise RuntimeError(
                f"Unable to safely split oversized part: {source_file}"
            )

        midpoint = part_duration / 2.0

        stem = Path(source_file).stem

        first_file = os.path.join(
            output_dir,
            f"{stem}_a.mp4",
        )

        second_file = os.path.join(
            output_dir,
            f"{stem}_b.mp4",
        )

        await create_part(
            source_file,
            0,
            midpoint,
            first_file,
        )

        await create_part(
            source_file,
            midpoint,
            part_duration - midpoint,
            second_file,
        )

        try:
            os.remove(source_file)
        except OSError:
            pass

        await process_part(
            first_file,
            part_number * 2,
        )

        await process_part(
            second_file,
            part_number * 2 + 1,
        )

    for index, part_file in enumerate(initial_files, start=1):
        await process_part(
            part_file,
            index,
        )

    if not final_files:
        raise RuntimeError("No final video parts were created")

    final_files.sort()

    return final_files


async def split_audio_for_telegram(
    media_file,
    temp_dir,
    max_part_bytes=47 * 1024 * 1024,
):
    """
    Split a large audio file into Telegram-safe parts.

    The first split is based on the total byte size and duration.
    Parts are validated afterwards, and any oversized part is
    recursively split again. Audio is stream-copied whenever possible;
    no re-encoding is performed.
    """
    if not os.path.isfile(media_file):
        raise FileNotFoundError(f"Audio file not found: {media_file}")

    total_size = os.path.getsize(media_file)

    if total_size <= max_part_bytes:
        return [media_file]

    split_root = os.path.join(temp_dir, "audio_parts")
    os.makedirs(split_root, exist_ok=True)

    duration_process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        media_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    duration_stdout, duration_stderr = await communicate_with_cleanup(
        duration_process,
        60,
    )

    if duration_process.returncode != 0:
        raise RuntimeError(
            "ffprobe failed while preparing audio split: "
            + duration_stderr.decode(errors="ignore")[-2000:]
        )

    duration_text = duration_stdout.decode(errors="ignore").strip()

    try:
        duration = float(duration_text)
    except (TypeError, ValueError):
        raise RuntimeError(
            f"Invalid audio duration returned by ffprobe: {duration_text!r}"
        )

    if duration <= 0:
        raise RuntimeError("Audio duration is not positive")

    # Initial estimate. A safety margin avoids producing parts too close
    # to Telegram's upload ceiling.
    initial_parts = max(
        2,
        (total_size + max_part_bytes - 1) // max_part_bytes,
    )

    segment_time = duration / initial_parts

    initial_pattern = os.path.join(
        split_root,
        "part_%04d.mp3",
    )

    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        media_file,
        "-map",
        "0",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(segment_time),
        "-reset_timestamps",
        "1",
        initial_pattern,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await communicate_with_cleanup(
        process,
        DOWNLOAD_TIMEOUT,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed while splitting audio: "
            + stderr.decode(errors="ignore")[-3000:]
        )

    generated = sorted(
        os.path.join(split_root, name)
        for name in os.listdir(split_root)
        if name.startswith("part_")
        and name.lower().endswith(".mp3")
        and os.path.isfile(os.path.join(split_root, name))
    )

    if not generated:
        raise RuntimeError("ffmpeg produced no audio parts")

    async def split_oversized_part(source_file, depth=0):
        if os.path.getsize(source_file) <= max_part_bytes:
            return [source_file]

        if depth >= 8:
            raise RuntimeError(
                "Unable to reduce an audio part below Telegram limit "
                f"after {depth} recursive splits: {source_file}"
            )

        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            source_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        probe_stdout, probe_stderr = await communicate_with_cleanup(
            probe,
            60,
        )

        if probe.returncode != 0:
            raise RuntimeError(
                "ffprobe failed on oversized audio part: "
                + probe_stderr.decode(errors="ignore")[-2000:]
            )

        try:
            part_duration = float(
                probe_stdout.decode(errors="ignore").strip()
            )
        except (TypeError, ValueError):
            raise RuntimeError(
                "Invalid duration for oversized audio part"
            )

        if part_duration <= 1:
            raise RuntimeError(
                "Oversized audio part cannot be safely split further"
            )

        recursive_dir = os.path.join(
            split_root,
            f"recursive_{depth}",
        )
        os.makedirs(recursive_dir, exist_ok=True)

        pattern = os.path.join(
            recursive_dir,
            f"part_{depth}_%04d.mp3",
        )

        child_process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            source_file,
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(part_duration / 2),
            "-reset_timestamps",
            "1",
            pattern,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, child_stderr = await communicate_with_cleanup(
            child_process,
            DOWNLOAD_TIMEOUT,
        )

        if child_process.returncode != 0:
            raise RuntimeError(
                "ffmpeg failed during recursive audio split: "
                + child_stderr.decode(errors="ignore")[-3000:]
            )

        children = sorted(
            os.path.join(recursive_dir, name)
            for name in os.listdir(recursive_dir)
            if name.startswith(f"part_{depth}_")
            and name.lower().endswith(".mp3")
            and os.path.isfile(os.path.join(recursive_dir, name))
        )

        if len(children) < 2:
            raise RuntimeError(
                "Recursive audio split did not produce multiple parts"
            )

        result = []
        for child in children:
            result.extend(
                await split_oversized_part(child, depth + 1)
            )

        return result

    final_parts = []
    for part in generated:
        final_parts.extend(
            await split_oversized_part(part)
        )

    # Final hard validation.
    final_parts = sorted(
        final_parts,
        key=lambda p: os.path.getmtime(p),
    )

    if not final_parts:
        raise RuntimeError("No final audio parts available")

    for part in final_parts:
        if not os.path.isfile(part):
            raise RuntimeError(
                f"Audio part disappeared before delivery: {part}"
            )

        part_size = os.path.getsize(part)
        if part_size > max_part_bytes:
            raise RuntimeError(
                f"Audio part exceeds Telegram-safe limit: "
                f"{part_size} bytes"
            )

    return final_parts


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
    # --------------------------------------------------------




    # --------------------------------------------------------
    # --------------------------------------------------------



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
            username=(
                f"@{user.username}"
                if user.username
                else {
                    "ar": "غير محدد",
                    "en": "Not specified",
                    "tr": "Belirtilmemiş",
                    "de": "Nicht angegeben",
                }.get(language, "غير محدد")
            ),
            website=website,
            quality=quality_name
        )
    )

    temp_dir = tempfile.mkdtemp(prefix="videobot_")

    # معرّف موحّد لمحاولة التحميل بالكامل.
    # كل مراحل المحاولة (yt-dlp / fallback / Yoinku / final)
    # يجب أن تستخدم نفس attempt_id حتى يستطيع Gemini تجميعها كحادثة واحدة.
    attempt_id = uuid.uuid4().hex
    attempt_number = 1
    attempt_started_at = time.monotonic()

    # Always initialize before entering try: finally must be safe on every path.


    # تتبع آخر خطأ في مسار التحميل
    last_error_stage = None
    last_error_type = None
    last_error_message = None
    yoinku_attempted = False

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

        if process.returncode != 0 or not media_file:

            # تسجيل فشل yt-dlp لتحليله لاحقًا بواسطة Gemini
            last_error_stage = "yt-dlp"
            last_error_type = "yt_dlp_failed"
            last_error_message = (
                stderr_text[-4000:]
                or stdout_text[-4000:]
                or f"yt-dlp exited with code {process.returncode}"
            )

            log_download_error(
                user_id=query.from_user.id if query.from_user else None,
                username=query.from_user.username if query.from_user else None,
                url=url,
                website=website,
                media_type="audio" if is_audio else "video",
                stage=last_error_stage,
                error_type=last_error_type,
                error_message=last_error_message,
                traceback_text=None,
                yoinku_used=False,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                duration_ms=int(
                    (time.monotonic() - attempt_started_at) * 1000
                ),
                return_code=process.returncode,
                exception_type="YtDlpProcessError",
                details={
                    "stdout_tail": sanitize_error_for_storage(
                        stdout_text[-2000:]
                    ),
                    "stderr_tail": sanitize_error_for_storage(
                        stderr_text[-2000:]
                    ),
                    "return_code": process.returncode,
                },
            )

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
            # Yoinku fallback
            # يتم تجربته قبل direct fallback لتقليل زمن الفشل.
            # ------------------------------------------------

            yoinku_attempted = True

            yoinku_file, yoinku_diagnostics = await download_with_yoinku(
                url=url,
                temp_dir=temp_dir,
                is_audio=is_audio,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
            )

            # ------------------------------------------------
            # محاولة استخراج مصدر مباشر من صفحة الموقع
            # فقط إذا فشل Yoinku.
            # ------------------------------------------------

            if yoinku_file:
                fallback_file = yoinku_file
                fallback_diagnostics = {}
            else:
                fallback_file, _, _, fallback_diagnostics = await download_with_fallback(
                    url=url,
                    temp_dir=temp_dir,
                    output_template=output_template,
                    format_option=format_option,
                    is_audio=is_audio,
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                )

            if fallback_file:

                media_file = fallback_file

                print()

                if yoinku_file:
                    print("===== YOINKU FALLBACK SUCCESS =====")
                else:
                    print("✅ FALLBACK DOWNLOAD SUCCESS")

                print(f"File: {media_file}")

                if yoinku_file:
                    print("===================================")
                else:
                    print("==============================")

                print()

            else:

                # ------------------------------------------------
                # Yoinku fallback
                # ------------------------------------------------

                # تسجيل فشل المصدر المباشر قبل تجربة Yoinku
                last_error_stage = "direct_fallback"
                last_error_type = "fallback_failed"
                last_error_message = (
                    "Direct media fallback failed to produce a usable file."
                )

                # استخراج آخر تشخيص متاح من direct fallback.
                fallback_candidates = (
                    fallback_diagnostics.get("candidates", [])
                    if isinstance(fallback_diagnostics, dict)
                    else []
                )

                last_fallback_candidate = (
                    fallback_candidates[-1]
                    if fallback_candidates
                    else {}
                )

                fallback_extraction_error = (
                    fallback_diagnostics.get("extraction_error")
                    if isinstance(fallback_diagnostics, dict)
                    else None
                )

                fallback_exception_type = (
                    (
                        fallback_extraction_error.get(
                            "exception_type"
                        )
                        if isinstance(
                            fallback_extraction_error,
                            dict,
                        )
                        else None
                    )
                    or last_fallback_candidate.get(
                        "exception_type"
                    )
                    or "DirectFallbackError"
                )

                fallback_error_message = (
                    (
                        fallback_extraction_error.get(
                            "error_message"
                        )
                        if isinstance(
                            fallback_extraction_error,
                            dict,
                        )
                        else None
                    )
                    or last_fallback_candidate.get(
                        "error_message"
                    )
                    or last_error_message
                )

                log_download_error(
                    user_id=query.from_user.id if query.from_user else None,
                    username=query.from_user.username if query.from_user else None,
                    url=url,
                    website=website,
                    media_type="audio" if is_audio else "video",
                    stage=last_error_stage,
                    error_type=last_error_type,
                    error_message=fallback_error_message,
                    traceback_text=None,
                    yoinku_used=yoinku_attempted,
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    duration_ms=(
                        fallback_diagnostics.get(
                            "total_duration_ms"
                        )
                        if isinstance(
                            fallback_diagnostics,
                            dict,
                        )
                        else None
                    ),
                    return_code=last_fallback_candidate.get(
                        "return_code"
                    ),
                    exception_type=fallback_exception_type,
                    http_status=last_fallback_candidate.get(
                        "http_status"
                    ),
                    response_type=last_fallback_candidate.get(
                        "response_type"
                    ),
                    bytes_downloaded=last_fallback_candidate.get(
                        "bytes_downloaded"
                    ),
                    candidate_index=last_fallback_candidate.get(
                        "candidate_index"
                    ),
                    candidate_count=(
                        fallback_diagnostics.get(
                            "candidate_count"
                        )
                        if isinstance(
                            fallback_diagnostics,
                            dict,
                        )
                        else None
                    ),
                    details=fallback_diagnostics,
                )

                print()
                # ------------------------------------------------
                # تسجيل فشل Yoinku بالتشخيص الكامل
                # ------------------------------------------------

                last_error_stage = "yoinku"
                last_error_type = "yoinku_failed"

                yoinku_error_message = (
                    yoinku_diagnostics.get("error_message")
                    if isinstance(yoinku_diagnostics, dict)
                    else None
                ) or (
                    "Yoinku fallback was attempted but did not return a usable file."
                )

                yoinku_exception_type = (
                    yoinku_diagnostics.get("exception_type")
                    if isinstance(yoinku_diagnostics, dict)
                    else None
                ) or "YoinkuFallbackError"

                log_download_error(
                    user_id=query.from_user.id if query.from_user else None,
                    username=query.from_user.username if query.from_user else None,
                    url=url,
                    website=website,
                    media_type="audio" if is_audio else "video",
                    stage=last_error_stage,
                    error_type=last_error_type,
                    error_message=yoinku_error_message,
                    traceback_text=None,
                    yoinku_used=yoinku_attempted,
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    duration_ms=(
                        yoinku_diagnostics.get("duration_ms")
                        if isinstance(yoinku_diagnostics, dict)
                        else None
                    ),
                    return_code=None,
                    exception_type=yoinku_exception_type,
                    http_status=(
                        yoinku_diagnostics.get("http_status")
                        if isinstance(yoinku_diagnostics, dict)
                        else None
                    ),
                    response_type=(
                        yoinku_diagnostics.get("response_type")
                        if isinstance(yoinku_diagnostics, dict)
                        else None
                    ),
                    bytes_downloaded=(
                        yoinku_diagnostics.get("bytes_downloaded")
                        if isinstance(yoinku_diagnostics, dict)
                        else None
                    ),
                    candidate_index=None,
                    candidate_count=None,
                    details=yoinku_diagnostics,
                )

                # ------------------------------------------------
                # تسجيل فشل جميع طرق التحميل
                # ------------------------------------------------

                log_download_error(
                    user_id=query.from_user.id if query.from_user else None,
                    username=query.from_user.username if query.from_user else None,
                    url=url,
                    website=website,
                    media_type="audio" if is_audio else "video",
                    stage="download",
                    error_type="all_methods_failed",
                    error_message="All available download methods failed.",
                    traceback_text=None,
                    yoinku_used=yoinku_attempted,
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    duration_ms=int(
                        (time.monotonic() - attempt_started_at) * 1000
                    ),
                    return_code=None,
                    exception_type="AllDownloadMethodsFailed",
                    http_status=None,
                    response_type=None,
                    bytes_downloaded=None,
                    candidate_index=None,
                    candidate_count=(
                        fallback_diagnostics.get("candidate_count")
                        if isinstance(
                            fallback_diagnostics,
                            dict,
                        )
                        else None
                    ),
                    details={
                        "fallback": fallback_diagnostics,
                        "yoinku": yoinku_diagnostics,
                    },
                )

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
        # ----------------------------------------------------
        # ----------------------------------------------------
        # إرسال الصوت مع تقسيم الملفات الكبيرة
        # ----------------------------------------------------
        if is_audio:
            await query.edit_message_text(
                TEXTS[language]["uploading"]
            )

            if not os.path.isfile(media_file):
                await query.edit_message_text(
                    TEXTS[language]["file_error"]
                )
                return

            final_size = os.path.getsize(media_file)
            print()
            print("===== FINAL AUDIO FILE =====")
            print(f"File: {media_file}")
            print(
                f"Audio size: "
                f"{final_size / 1024 / 1024:.2f} MB"
            )
            print("============================")
            print()

            audio_parts = await split_audio_for_telegram(
                media_file=media_file,
                temp_dir=temp_dir,
                max_part_bytes=MAX_TELEGRAM_AUDIO_BYTES,
            )

            total_parts = len(audio_parts)

            print("===== AUDIO DELIVERY =====")
            print(f"Parts: {total_parts}")

            for part_index, part_file in enumerate(
                audio_parts,
                start=1,
            ):
                part_size = os.path.getsize(part_file)

                if part_size > MAX_TELEGRAM_AUDIO_BYTES:
                    raise RuntimeError(
                        "Audio part exceeds Telegram-safe size: "
                        f"{part_size} bytes"
                    )

                print(
                    f"Part {part_index}/{total_parts}: "
                    f"{part_size / 1024 / 1024:.2f} MB"
                )

                if total_parts == 1:
                    caption = (
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
                            ),
                        )
                    )
                else:
                    caption = (
                        f"🎵 الجزء {part_index} من {total_parts}\n"
                        + TEXTS[language]["audio_done"]
                        .format(
                            quality=quality_name,
                            username=(
                                f"@{user.username}"
                                if user.username
                                else (
                                    user.first_name
                                    or "صديقي"
                                )
                            ),
                        )
                    )

                with open(part_file, "rb") as audio:
                    await context.bot.send_audio(
                        chat_id=update.effective_chat.id,
                        audio=audio,
                        caption=caption,
                        read_timeout=600,
                        write_timeout=600,
                        connect_timeout=60,
                        pool_timeout=60,
                    )

            print("==========================")
            print()

        # ----------------------------------------------------
        # إرسال الفيديو مباشرة بدون ضغط أو تخفيض جودة
        # ----------------------------------------------------

        # ----------------------------------------------------
        else:
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

            await query.edit_message_text(
                TEXTS[language]["uploading"]
            )

            username_display = (
                f"@{user.username}"
                if user.username
                else (
                    user.first_name
                    or "صديقي"
                )
            )

            video_caption = (
                TEXTS[language]["video_done"]
                .format(
                    quality=quality_name,
                    username=username_display,
                )
            )

            if video_size_bytes <= MAX_TELEGRAM_VIDEO_BYTES:
                # فيديو ضمن الحد: إرساله كما هو بدون أي تعديل.
                with open(
                    media_file,
                    "rb",
                ) as video:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=video,
                        caption=video_caption,
                        read_timeout=600,
                        write_timeout=600,
                        connect_timeout=60,
                        pool_timeout=60,
                    )
            else:
                # فيديو أكبر من الحد: تقسيمه بدون إعادة ترميز.
                split_dir = os.path.join(
                    temp_dir,
                    "video_parts",
                )

                # 47 MB حد داخلي آمن، مع إبقاء Telegram limit عند 49 MB.
                split_limit_bytes = 47 * 1024 * 1024

                part_files = await split_video_for_telegram(
                    media_file=media_file,
                    output_dir=split_dir,
                    max_part_bytes=split_limit_bytes,
                )

                total_parts = len(part_files)

                for part_index, part_file in enumerate(
                    part_files,
                    start=1,
                ):
                    part_size = os.path.getsize(part_file)

                    if part_size > MAX_TELEGRAM_VIDEO_BYTES:
                        raise RuntimeError(
                            "A generated video part still exceeds "
                            f"Telegram limit: {part_file}"
                        )

                    part_caption = (
                        f"📹 الجزء {part_index} من {total_parts}\n"
                        f"{video_caption}"
                    )

                    with open(
                        part_file,
                        "rb",
                    ) as video_part:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=video_part,
                            caption=part_caption,
                            read_timeout=600,
                            write_timeout=600,
                            connect_timeout=60,
                            pool_timeout=60,
                        )
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
        timeout_duration_ms = int(
            (time.monotonic() - attempt_started_at) * 1000
        )

        log_download_error(
            user_id=query.from_user.id if query.from_user else None,
            username=query.from_user.username if query.from_user else None,
            url=url,
            website=website,
            media_type="audio" if is_audio else "video",
            stage=last_error_stage or "download",
            error_type=last_error_type or "timeout",
            error_message=(
                last_error_message
                or "Download operation timed out"
            ),
            traceback_text=None,
            yoinku_used=yoinku_attempted,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            duration_ms=timeout_duration_ms,
            return_code=None,
            exception_type="TimeoutError",
            http_status=None,
            response_type=None,
            bytes_downloaded=None,
            candidate_index=None,
            candidate_count=None,
            details={
                "timeout": True,
                "duration_ms": timeout_duration_ms,
            },
        )

        try:
            await query.edit_message_text(
                TEXTS[language]["download_error"]
            )
        except Exception:
            pass

    except Exception as e:
        import traceback as _traceback

        error_traceback = _traceback.format_exc()

        exception_duration_ms = int(
            (time.monotonic() - attempt_started_at) * 1000
        )

        log_download_error(
            user_id=query.from_user.id if query.from_user else None,
            username=query.from_user.username if query.from_user else None,
            url=url,
            website=website,
            media_type="audio" if is_audio else "video",
            stage=last_error_stage or "download",
            error_type=last_error_type or type(e).__name__,
            error_message=(
                last_error_message
                or sanitize_error_for_storage(str(e))
            ),
            traceback_text=error_traceback,
            yoinku_used=yoinku_attempted,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            duration_ms=exception_duration_ms,
            return_code=None,
            exception_type=type(e).__name__,
            http_status=None,
            response_type=None,
            bytes_downloaded=None,
            candidate_index=None,
            candidate_count=None,
            details={
                "exception": sanitize_error_for_storage(
                    repr(e)
                ),
                "duration_ms": exception_duration_ms,
            },
        )

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
                "🤖 الذكاء الاصطناعي",
                callback_data="admin_ai"
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
# الذكاء الاصطناعي - لوحة الإدارة
# ============================================================

async def admin_ai_callback(
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
                "📊 تحليل الإحصائيات",
                callback_data="ai_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "🧪 اختبار Gemini",
                callback_data="ai_test"
            )
        ],
        [
            InlineKeyboardButton(
                "📈 تقرير أداء البوت",
                callback_data="ai_report"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 تحليل المستخدمين",
                callback_data="ai_users"
            )
        ],
        [
            InlineKeyboardButton(
                "🌐 تحليل المنصات",
                callback_data="ai_websites"
            )
        ],
        [
            InlineKeyboardButton(
                "🐞 تقرير الأخطاء",
                callback_data="ai_errors"
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
        "🤖 الذكاء الاصطناعي\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "اختر الخدمة التي تريد تنفيذها:",
        reply_markup=keyboard
    )



async def admin_ai_test_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    await query.edit_message_text(
        "🤖 جاري اختبار اتصال Gemini..."
    )

    try:

        result = await gemini_generate(
            "أجب بكلمة واحدة فقط: متصل"
        )

        result = result.strip()

        await query.edit_message_text(
            "🤖 اختبار Gemini\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "✅ الاتصال ناجح\n\n"
            f"الرد: {result}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 الذكاء الاصطناعي",
                        callback_data="admin_ai"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 لوحة الإدارة",
                        callback_data="admin_home"
                    )
                ],
            ])
        )

    except Exception as e:

        logger.exception(
            "Gemini test failed: %s",
            str(e)
        )

        error_text = str(e)
        if len(error_text) > 1200:
            error_text = error_text[:1200] + "..."

        await query.edit_message_text(
            "🤖 اختبار Gemini\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ فشل الاتصال بـ Gemini.\n\n"
            f"نوع الخطأ: {type(e).__name__}\n"
            f"تفاصيل الخطأ:\n{html.escape(error_text)}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 إعادة الاختبار",
                        callback_data="ai_test"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 الذكاء الاصطناعي",
                        callback_data="admin_ai"
                    )
                ],
            ])
        )



# ============================================================
# تحليلات Gemini - لوحة الإدارة
# ============================================================

def ai_admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 إعادة التحليل",
                callback_data="admin_ai_refresh"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 الذكاء الاصطناعي",
                callback_data="admin_ai"
            ),
            InlineKeyboardButton(
                "🏠 الرئيسية",
                callback_data="admin_home"
            )
        ],
    ])


def get_ai_statistics_data():
    data = get_statistics()

    websites = [
        {
            "website": row["website"],
            "count": row["count"]
        }
        for row in data["websites"]
    ]

    languages = [
        {
            "language": row["language"],
            "count": row["count"]
        }
        for row in data["languages"]
    ]

    genders = [
        {
            "gender": row["gender"],
            "count": row["count"]
        }
        for row in data["genders"]
    ]

    return {
        "users": data["users"],
        "banned": data["banned"],
        "downloads": data["downloads"],
        "videos": data["videos"],
        "audio": data["audio"],
        "phones": data["phones"],
        "locations": data["locations"],
        "websites": websites,
        "languages": languages,
        "genders": genders,
    }


def get_ai_users_data():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN is_banned = 1 THEN 1 ELSE 0 END) AS banned,
            SUM(CASE WHEN downloads > 0 THEN 1 ELSE 0 END) AS active,
            AVG(downloads) AS average_downloads,
            MAX(downloads) AS max_downloads
        FROM users
    """)

    summary = cur.fetchone()

    cur.execute("""
        SELECT
            language,
            COUNT(*) AS count
        FROM users
        GROUP BY language
        ORDER BY count DESC
    """)

    languages = [
        {
            "language": row["language"],
            "count": row["count"]
        }
        for row in cur.fetchall()
    ]

    cur.execute("""
        SELECT
            user_id,
            username,
            first_name,
            downloads
        FROM users
        ORDER BY downloads DESC
        LIMIT 10
    """)

    top_users = [
        {
            "user_id": row["user_id"],
            "username": row["username"] or "",
            "first_name": row["first_name"] or "",
            "downloads": row["downloads"] or 0
        }
        for row in cur.fetchall()
    ]

    conn.close()

    return {
        "total": summary["total"] or 0,
        "banned": summary["banned"] or 0,
        "active": summary["active"] or 0,
        "average_downloads": round(
            summary["average_downloads"] or 0,
            2
        ),
        "max_downloads": summary["max_downloads"] or 0,
        "languages": languages,
        "top_users": top_users,
    }


def get_ai_websites_data():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            website,
            COUNT(*) AS count
        FROM downloads
        GROUP BY website
        ORDER BY count DESC
    """)

    rows = cur.fetchall()

    total = sum(
        row["count"]
        for row in rows
    )

    websites = []

    for row in rows:
        count = row["count"]

        websites.append({
            "website": row["website"],
            "downloads": count,
            "percentage": round(
                (count / total * 100)
                if total else 0,
                2
            )
        })

    conn.close()

    return {
        "total_downloads": total,
        "websites": websites,
    }


def get_ai_report_data():
    conn = get_db()
    cur = conn.cursor()

    data = get_admin_dashboard_data(30)

    cur.execute("""
        SELECT
            website,
            COUNT(*) AS count
        FROM downloads
        WHERE created_at >= ?
        GROUP BY website
        ORDER BY count DESC
        LIMIT 10
    """, (
        datetime.fromtimestamp(
            datetime.now().timestamp() - (30 * 86400)
        ).isoformat(),
    ))

    websites = [
        {
            "website": row["website"],
            "count": row["count"]
        }
        for row in cur.fetchall()
    ]

    conn.close()

    return {
        "period_days": 30,
        "total_users": data["total_users"],
        "banned_users": data["banned_users"],
        "active_users": data["active_users"],
        "period_downloads": data["period_downloads"],
        "period_videos": data["period_videos"],
        "period_audio": data["period_audio"],
        "websites": websites,
    }


async def run_admin_ai_analysis(
    query,
    title,
    prompt
):
    await query.edit_message_text(
        "🤖 Gemini AI\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ جاري تحليل البيانات...\n"
        "يرجى الانتظار."
    )

    try:
        result = await gemini_generate(prompt)

        result = result.strip()

        if not result:
            result = "لم يُرجع Gemini نتيجة."

        # Telegram message limit protection
        if len(result) > 3900:
            result = result[:3900] + "\n\n… تم اختصار التقرير."

        await query.edit_message_text(
            f"{title}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"{result}",
            reply_markup=ai_admin_keyboard()
        )

    except Exception as e:
        logger.exception(
            "Gemini admin analysis failed"
        )

        await query.edit_message_text(
            "🤖 Gemini AI\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ تعذر تنفيذ التحليل.\n\n"
            f"الخطأ: {type(e).__name__}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 إعادة المحاولة",
                        callback_data="ai_retry"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 الذكاء الاصطناعي",
                        callback_data="admin_ai"
                    )
                ],
            ])
        )


async def admin_ai_errors_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:
        data = get_ai_errors_data(30)

        if data["total_error_records"] == 0:
            await query.edit_message_text(
                "🐞 تقرير أخطاء البوت\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "✅ لا توجد أخطاء مسجلة خلال آخر 30 يوماً.\n\n"
                "سيتم تسجيل أخطاء التحميل تلقائياً عند حدوثها.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔄 تحديث",
                            callback_data="ai_errors"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔙 الذكاء الاصطناعي",
                            callback_data="admin_ai"
                        ),
                        InlineKeyboardButton(
                            "🏠 الرئيسية",
                            callback_data="admin_home"
                        )
                    ],
                ])
            )
            return

        prompt = f"""
أنت مهندس Reliability وBackend متخصص في Telegram bots
وأنظمة تحميل الوسائط باستخدام yt-dlp وFFmpeg وواجهات fallback.

هذه بيانات أخطاء حقيقية من VideoBot خلال آخر 30 يوماً:

{data}

أنشئ تقريراً تقنياً باللغة العربية.

يجب أن يتضمن التقرير:

1. 🔴 الحالة العامة
- إجمالي الأخطاء.
- تقييم مستوى الأخطاء بناءً على البيانات فقط.

2. 🌐 تحليل المنصات
- أكثر المنصات تسبباً بالأخطاء.
- عدد الأخطاء لكل منصة عندما تكون البيانات متوفرة.

3. ⚙️ مراحل الفشل
- حدد أكثر مراحل النظام فشلاً.
- مثل yt-dlp أو fallback أو Yoinku أو download_media أو غيرها.

4. 🧩 أنواع الأخطاء
- حدد الأخطاء الأكثر تكراراً.
- اشرح معناها تقنياً.

5. 🔎 السبب المحتمل
- اربط نوع الخطأ بالمرحلة والمنصة ورسالة الخطأ.
- إذا لم تكن البيانات كافية، قل بوضوح إن السبب غير مؤكد.

6. 🛠️ الحل المقترح
- قدم حلولاً عملية للمطور.
- رتب الحلول حسب الأولوية.

7. 🚨 الأولوية
صنف المشاكل:
- حرجة
- عالية
- متوسطة
- منخفضة

8. 📋 آخر الأخطاء
- لخص أهم الأخطاء الحديثة.
- اذكر المنصة والمرحلة ونوع الخطأ والمشكلة.

قواعد صارمة:
- استخدم البيانات المعطاة فقط.
- لا تخترع أرقاماً.
- لا تخترع أخطاء غير موجودة.
- لا تذكر API keys أو tokens أو كلمات مرور.
- لا تحاول تحديد هوية المستخدمين.
- لا تقترح تنفيذ أوامر تلقائياً على الخادم.
- هذا التقرير للتشخيص فقط.
"""

        await run_admin_ai_analysis(
            query,
            "🐞 تقرير أخطاء البوت",
            prompt
        )

    except Exception as exc:
        logger.exception(
            "AI error report failed: %s",
            type(exc).__name__
        )

        await query.edit_message_text(
            "🐞 تقرير أخطاء البوت\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ تعذر تحميل تقرير الأخطاء.\n\n"
            f"نوع الخطأ: {type(exc).__name__}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 إعادة المحاولة",
                        callback_data="ai_errors"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 الذكاء الاصطناعي",
                        callback_data="admin_ai"
                    )
                ],
            ])
        )


async def admin_ai_stats_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    data = get_ai_statistics_data()

    prompt = f"""
أنت محلل بيانات ومدير تقني لبوت تيليجرام لتحميل الوسائط.

حلل بيانات البوت التالية:

{data}

اكتب تقريراً عربياً واضحاً ومختصراً يتضمن:
1. ملخص الحالة الحالية.
2. أهم الأرقام.
3. مستوى نشاط المستخدمين.
4. أكثر أنواع الاستخدام.
5. أهم الملاحظات أو المشاكل المحتملة.
6. 3 توصيات عملية لتحسين البوت.

لا تخترع أي أرقام غير موجودة في البيانات.
لا تذكر مفتاح API أو أي معلومات سرية.
"""

    await run_admin_ai_analysis(
        query,
        "📊 تحليل الإحصائيات",
        prompt
    )


async def admin_ai_report_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    data = get_ai_report_data()

    prompt = f"""
أنت مستشار تقني متخصص في تحليل أداء بوتات Telegram.

هذه بيانات البوت خلال آخر 30 يوماً:

{data}

أنشئ تقرير أداء احترافي باللغة العربية يتضمن:
- تقييم عام للأداء.
- نشاط المستخدمين.
- أداء التحميلات.
- مقارنة الفيديو والصوت.
- المنصات الأكثر استخداماً.
- نقاط القوة.
- نقاط الضعف المحتملة.
- 5 توصيات عملية قابلة للتنفيذ.

اعتمد فقط على البيانات المعطاة ولا تخترع أرقاماً.
"""

    await run_admin_ai_analysis(
        query,
        "📈 تقرير أداء البوت",
        prompt
    )


async def admin_ai_users_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    data = get_ai_users_data()

    prompt = f"""
أنت محلل بيانات لبوت Telegram.

حلل بيانات المستخدمين التالية:

{data}

أعطني تحليلاً عربياً يتضمن:
- حجم قاعدة المستخدمين.
- نسبة النشاط.
- نسبة المحظورين.
- متوسط التحميلات.
- المستخدمون الأكثر نشاطاً.
- توزيع اللغات.
- ملاحظات إدارية مفيدة.
- 3 اقتراحات لزيادة استخدام البوت.

لا تحاول تحديد هوية الأشخاص ولا تستنتج معلومات شخصية غير موجودة.
لا تخترع أرقاماً.
"""

    await run_admin_ai_analysis(
        query,
        "👥 تحليل المستخدمين",
        prompt
    )


async def admin_ai_websites_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    data = get_ai_websites_data()

    prompt = f"""
أنت محلل استخدام لمنصة تحميل Telegram.

حلل بيانات المنصات التالية:

{data}

اكتب تحليلاً عربياً يتضمن:
- ترتيب المنصات حسب الاستخدام.
- نسبة كل منصة.
- المنصات المسيطرة.
- ملاحظات عن تنوع مصادر التحميل.
- اقتراحات تقنية لتحسين دعم المنصات الأكثر استخداماً.

لا تخترع أي بيانات غير موجودة.
"""

    await run_admin_ai_analysis(
        query,
        "🌐 تحليل المنصات",
        prompt
    )


async def admin_ai_refresh_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    await admin_ai_callback(
        update,
        context
    )


async def admin_ai_retry_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    await admin_ai_callback(
        update,
        context
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

        "🛠️ لوحة إدارة AliBot\n"
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

    context.user_data.clear()


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
        CallbackQueryHandler(
            start_button_callback,
            pattern=r"^start_button$"
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
            admin_ai_callback,
            pattern=r"^admin_ai$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_ai_test_callback,
            pattern=r"^ai_test$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_ai_stats_callback,
            pattern=r"^ai_stats$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_ai_report_callback,
            pattern=r"^ai_report$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_ai_users_callback,
            pattern=r"^ai_users$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_ai_websites_callback,
            pattern=r"^ai_websites$"
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            admin_ai_errors_callback,
            pattern=r"^ai_errors$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_ai_refresh_callback,
            pattern=r"^admin_ai_refresh$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_ai_retry_callback,
            pattern=r"^ai_retry$"
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
            pattern=r"^(video_|audio_|main_menu)"
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
# لوحة الإدارة المتقدمة - AliBot
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
        "📊 لوحة معلومات AliBot\n"
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
