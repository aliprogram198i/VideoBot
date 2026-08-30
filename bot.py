from pathlib import Path
import os
import asyncio
import sqlite3
import tempfile
import shutil
import html
from datetime import datetime
from urllib.parse import urlparse

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
VOLUME_DB_FILE = "/data/bot_stats.db"

if Path("/data").is_dir():
    volume_db = Path(VOLUME_DB_FILE)
    local_db = Path(LOCAL_DB_FILE)

    if not volume_db.exists() and local_db.exists():
        import shutil
        shutil.copy2(local_db, volume_db)
        print("✅ Existing database copied to Railway Volume.")

DB_FILE = VOLUME_DB_FILE if Path("/data").is_dir() else LOCAL_DB_FILE

DOWNLOAD_TIMEOUT = 900
MAX_BROADCAST_LENGTH = 4000


# ============================================================
# النصوص
# ============================================================

TEXTS = {

    "ar": {

        "choose_language":
            "🏠 أهلاً وسهلاً بك في بوت التحميل ❤️\n\n"
            "🎬 حمّل فيديوهاتك وصوتياتك بسهولة وسرعة.\n"
            "⚡ جودة متعددة\n"
            "🎵 تحويل الفيديو إلى صوت\n"
            "🌍 دعم عدة منصات\n\n"
            "🌐 اختر لغة البوت:",

        "welcome":
            "🎬 أهلاً وسهلاً بك في بوت التحميل ❤️\n\n"
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
            "🎬 تم تحميل الفيديو بنجاح!\n"
            "🎚 الجودة: {quality}\n\n"
            "❤️ لا تنسَ مشاركة رابط البوت مع أصدقائك.\n"
            "🔗 ساعدنا في الوصول إلى المزيد من الأشخاص!",

        "audio_done":
            "🎵 تم تحميل الصوت بنجاح!\n"
            "🎚 الجودة: {quality}\n\n"
            "❤️ لا تنسَ مشاركة رابط البوت مع أصدقائك.\n"
            "🔗 شارك البوت ليستفيد منه أصدقاؤك أيضاً!",

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

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM downloads WHERE user_id = ?",
        (user_id,)
    )

    cur.execute(
        "DELETE FROM users WHERE user_id = ?",
        (user_id,)
    )

    conn.commit()
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

    if not url.startswith(
        ("http://", "https://")
    ):

        await update.message.reply_text(
            TEXTS[language]["invalid_url"]
        )

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
# التحميل
# ============================================================

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
            "bestvideo+bestaudio/best"
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

    temp_dir = tempfile.mkdtemp(
        prefix="videobot_"
    )

    try:

        output_template = os.path.join(
            temp_dir,
            "%(title)s.%(ext)s"
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

            "--newline",

            "--no-warnings",

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
        print(" ".join(command))
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

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=DOWNLOAD_TIMEOUT
        )

        stdout_text = stdout.decode(
            errors="ignore"
        )

        stderr_text = stderr.decode(
            errors="ignore"
        )

        print(stdout_text)

        if stderr_text:

            print()
            print("===== yt-dlp STDERR =====")
            print(stderr_text)
            print("=========================")
            print()

        if process.returncode != 0:

            await query.edit_message_text(
                TEXTS[language]["download_error"]
            )

            return

        # ----------------------------------------------------
        # البحث عن الملف
        # ----------------------------------------------------

        media_file = None

        if is_audio:

            allowed_extensions = (
                ".mp3",
                ".m4a",
                ".opus",
                ".aac",
                ".wav",
            )

        else:

            allowed_extensions = (
                ".mp4",
                ".mkv",
                ".webm",
                ".mov",
            )

        for filename in os.listdir(
            temp_dir
        ):

            full_path = os.path.join(
                temp_dir,
                filename
            )

            if not os.path.isfile(
                full_path
            ):
                continue

            if filename.lower().endswith(
                allowed_extensions
            ):

                media_file = full_path

                break

        if not media_file:

            await query.edit_message_text(
                TEXTS[language]["file_error"]
            )

            return

        # ----------------------------------------------------
        # إرسال الملف
        # ----------------------------------------------------

        await query.edit_message_text(
            TEXTS[language]["uploading"]
        )

        if is_audio:

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
                            quality=quality_name
                        )
                    ),

                    read_timeout=600,
                    write_timeout=600,
                    connect_timeout=60,
                    pool_timeout=60,
                )

        else:

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
                            quality=quality_name
                        )
                    ),

                    supports_streaming=True,

                    read_timeout=600,
                    write_timeout=600,
                    connect_timeout=60,
                    pool_timeout=60,
                )

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
                "📊 الإحصائيات",
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

            await context.bot.send_message(
                chat_id=row["user_id"],
                text=message,
            )

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
        INSERT INTO broadcast_logs (
            admin_id,
            message,
            sent_count,
            failed_count,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        ADMIN_ID,
        message,
        sent,
        failed,
        datetime.now().isoformat(),
    ))

    conn.commit()
    conn.close()

    await status_message.edit_text(

        "✅ انتهى إرسال الإعلان.\n\n"

        f"📨 تم الإرسال: {sent}\n"
        f"❌ فشل الإرسال: {failed}\n"
        f"👥 الإجمالي: {len(users)}"
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

async def admin_home_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    await query.edit_message_text(

        "🛠️ لوحة إدارة بوت التحميل\n\n"

        "📊 الإحصائيات\n"
        "👥 إدارة المستخدمين\n"
        "📢 الإعلانات\n"
        "🔎 البحث عن المستخدمين\n\n"

        "اختر القسم:",

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

    await update.message.reply_text(
        "✅ تم إلغاء العملية."
    )


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

        save_phone(
            user.id,
            contact.phone_number
        )

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
            message_user_callback,
            pattern=r"^message_user_"
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
# بدء التشغيل
# ============================================================

if __name__ == "__main__":
    main()
