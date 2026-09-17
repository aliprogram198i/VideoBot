"""User Experience v2: per-user preferences and a personal favorites library."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes

_ALLOWED = {
    "video": ("video_best", "video_1080", "video_720", "video_480", "video_360"),
    "audio": ("audio_best", "audio_320", "audio_256", "audio_192", "audio_128"),
}

_LABELS = {
    "ar": {"video_best": "أفضل جودة", "video_1080": "1080p", "video_720": "720p", "video_480": "480p", "video_360": "360p", "audio_best": "أفضل جودة", "audio_320": "320 kbps", "audio_256": "256 kbps", "audio_192": "192 kbps", "audio_128": "128 kbps"},
    "en": {"video_best": "Best quality", "video_1080": "1080p", "video_720": "720p", "video_480": "480p", "video_360": "360p", "audio_best": "Best quality", "audio_320": "320 kbps", "audio_256": "256 kbps", "audio_192": "192 kbps", "audio_128": "128 kbps"},
    "tr": {"video_best": "En iyi kalite", "video_1080": "1080p", "video_720": "720p", "video_480": "480p", "video_360": "360p", "audio_best": "En iyi kalite", "audio_320": "320 kbps", "audio_256": "256 kbps", "audio_192": "192 kbps", "audio_128": "128 kbps"},
    "de": {"video_best": "Beste Qualität", "video_1080": "1080p", "video_720": "720p", "video_480": "480p", "video_360": "360p", "audio_best": "Beste Qualität", "audio_320": "320 kbps", "audio_256": "256 kbps", "audio_192": "192 kbps", "audio_128": "128 kbps"},
}

_MESSAGES = {
    "ar": {"settings": "⚙️ <b>إعدادات التحميل</b>", "type": "🎛️ النوع", "video": "فيديو", "audio": "صوت", "quality": "📐 الجودة", "saved": "سيحتفظ AliBot بهذه الإعدادات لهذا المستخدم.", "current_quality": "⚙️ الجودة الحالية", "library": "📚 مكتبتي", "back": "🔙 رجوع", "choose_quality": "📐 <b>اختر الجودة الافتراضية</b>", "library_title": "📚 <b>مكتبتي</b>", "library_empty": "لا توجد عناصر محفوظة بعد.", "saved_link": "رابط محفوظ", "save_alert": "⭐ تم الحفظ في مكتبتك.", "no_url": "لا يوجد رابط لحفظه.", "invalid_url": "تعذر التحقق من الرابط.", "not_found": "❌ العنصر غير موجود.", "saved_url_invalid": "❌ تعذر التحقق من الرابط المحفوظ.", "reload": "🔁 <b>إعادة تحميل من المكتبة</b>\n\nاختر النوع:", "use_preferences": "⚙️ استخدام الإعدادات", "ready": "❌ لا يوجد رابط جاهز."},
    "en": {"settings": "⚙️ <b>Download settings</b>", "type": "🎛️ Type", "video": "Video", "audio": "Audio", "quality": "📐 Quality", "saved": "AliBot will keep these settings for this user.", "current_quality": "⚙️ Current quality", "library": "📚 My library", "back": "🔙 Back", "choose_quality": "📐 <b>Choose the default quality</b>", "library_title": "📚 <b>My library</b>", "library_empty": "No saved items yet.", "saved_link": "Saved link", "save_alert": "⭐ Saved to your library.", "no_url": "No link is available to save.", "invalid_url": "Unable to validate the link.", "not_found": "❌ Item not found.", "saved_url_invalid": "❌ Unable to validate the saved link.", "reload": "🔁 <b>Reload from library</b>\n\nChoose the type:", "use_preferences": "⚙️ Use settings", "ready": "❌ No link is ready."},
    "tr": {"settings": "⚙️ <b>İndirme ayarları</b>", "type": "🎛️ Tür", "video": "Video", "audio": "Ses", "quality": "📐 Kalite", "saved": "AliBot bu ayarları bu kullanıcı için saklar.", "current_quality": "⚙️ Mevcut kalite", "library": "📚 Kitaplığım", "back": "🔙 Geri", "choose_quality": "📐 <b>Varsayılan kaliteyi seçin</b>", "library_title": "📚 <b>Kitaplığım</b>", "library_empty": "Henüz kayıtlı öğe yok.", "saved_link": "Kayıtlı bağlantı", "save_alert": "⭐ Kitaplığınıza kaydedildi.", "no_url": "Kaydedilecek bağlantı yok.", "invalid_url": "Bağlantı doğrulanamadı.", "not_found": "❌ Öğe bulunamadı.", "saved_url_invalid": "❌ Kayıtlı bağlantı doğrulanamadı.", "reload": "🔁 <b>Kitaplıktan yeniden indir</b>\n\nTürü seçin:", "use_preferences": "⚙️ Ayarları kullan", "ready": "❌ Hazır bir bağlantı yok."},
    "de": {"settings": "⚙️ <b>Download-Einstellungen</b>", "type": "🎛️ Typ", "video": "Video", "audio": "Audio", "quality": "📐 Qualität", "saved": "AliBot speichert diese Einstellungen für diesen Benutzer.", "current_quality": "⚙️ Aktuelle Qualität", "library": "📚 Meine Bibliothek", "back": "🔙 Zurück", "choose_quality": "📐 <b>Standardqualität auswählen</b>", "library_title": "📚 <b>Meine Bibliothek</b>", "library_empty": "Noch keine Elemente gespeichert.", "saved_link": "Gespeicherter Link", "save_alert": "⭐ In Ihrer Bibliothek gespeichert.", "no_url": "Kein Link zum Speichern verfügbar.", "invalid_url": "Der Link konnte nicht validiert werden.", "not_found": "❌ Element nicht gefunden.", "saved_url_invalid": "❌ Der gespeicherte Link konnte nicht validiert werden.", "reload": "🔁 <b>Aus der Bibliothek erneut herunterladen</b>\n\nWählen Sie den Typ:", "use_preferences": "⚙️ Einstellungen verwenden", "ready": "❌ Kein Link ist bereit."},
}

_FAV_RE = re.compile(r"^ux_fav_(\d+)$")
_LOAD_RE = re.compile(r"^ux_load_(\d+)$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _language(bot_module: Any, user_id: int) -> str:
    language = bot_module.get_language(user_id) or "ar"
    return language if language in _MESSAGES else "ar"


def _db(bot_module: Any):
    conn = bot_module.get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS user_preferences (
        user_id INTEGER PRIMARY KEY,
        media_type TEXT NOT NULL DEFAULT 'video',
        quality TEXT NOT NULL DEFAULT 'video_720',
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        url TEXT NOT NULL,
        website TEXT,
        media_type TEXT,
        quality TEXT,
        title TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, url, media_type, quality)
    )""")
    conn.commit()
    return conn


def _get_preferences(bot_module: Any, user_id: int) -> tuple[str, str]:
    conn = _db(bot_module)
    try:
        row = conn.execute("SELECT media_type, quality FROM user_preferences WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            conn.execute("INSERT INTO user_preferences(user_id, media_type, quality, updated_at) VALUES (?, 'video', 'video_720', ?)", (user_id, _now()))
            conn.commit()
            return "video", "video_720"
        media_type, quality = str(row["media_type"]), str(row["quality"])
        if media_type not in _ALLOWED or quality not in _ALLOWED[media_type]:
            return "video", "video_720"
        return media_type, quality
    finally:
        conn.close()


def _set_preferences(bot_module: Any, user_id: int, media_type: str, quality: str) -> None:
    if media_type not in _ALLOWED or quality not in _ALLOWED[media_type]:
        raise ValueError("invalid preference")
    conn = _db(bot_module)
    try:
        conn.execute("""INSERT INTO user_preferences(user_id, media_type, quality, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET media_type=excluded.media_type, quality=excluded.quality, updated_at=excluded.updated_at""", (user_id, media_type, quality, _now()))
        conn.commit()
    finally:
        conn.close()


def _settings_keyboard(language: str, media_type: str, quality: str) -> InlineKeyboardMarkup:
    msg = _MESSAGES[language]
    labels = _LABELS[language]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 " + msg["video"] + (" ✅" if media_type == "video" else ""), callback_data="ux_set_video")],
        [InlineKeyboardButton("🎵 " + msg["audio"] + (" ✅" if media_type == "audio" else ""), callback_data="ux_set_audio")],
        [InlineKeyboardButton(f"{msg['current_quality']}: {labels.get(quality, '—')}", callback_data="ux_quality_menu")],
        [InlineKeyboardButton(msg["library"], callback_data="ux_library")],
        [InlineKeyboardButton(msg["back"], callback_data="main_menu")],
    ])


def _quality_keyboard(language: str, media_type: str) -> InlineKeyboardMarkup:
    labels = _LABELS[language]
    msg = _MESSAGES[language]
    return InlineKeyboardMarkup([[InlineKeyboardButton(labels[value], callback_data=f"ux_quality_{value}")] for value in _ALLOWED[media_type]] + [[InlineKeyboardButton(msg["back"], callback_data="ux_settings")]])


async def _settings_screen(target, bot_module: Any, user_id: int) -> None:
    language = _language(bot_module, user_id)
    msg = _MESSAGES[language]
    labels = _LABELS[language]
    media_type, quality = _get_preferences(bot_module, user_id)
    text = (
        f"{msg['settings']}\n━━━━━━━━━━━━━━━━━━\n\n"
        f"{msg['type']}: <b>{msg['video'] if media_type == 'video' else msg['audio']}</b>\n"
        f"{msg['quality']}: <b>{html.escape(labels.get(quality, '—'))}</b>\n\n"
        f"{msg['saved']}"
    )
    keyboard = _settings_keyboard(language, media_type, quality)
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await target.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return
    bot_module = __import__("bot")
    if bot_module.is_banned(user.id):
        return
    await _settings_screen(update.message, bot_module, user.id)


async def _library_screen(target, bot_module: Any, user_id: int) -> None:
    language = _language(bot_module, user_id)
    msg = _MESSAGES[language]
    labels = _LABELS[language]
    conn = _db(bot_module)
    try:
        rows = conn.execute(
            "SELECT id, website, title, media_type, quality FROM user_favorites "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        await target.reply_text(f"{msg['library_title']}\n\n{msg['library_empty']}", parse_mode="HTML")
        return
    lines = [msg["library_title"], "━━━━━━━━━━━━━━━━━━", ""]
    keyboard = []
    for index, row in enumerate(rows):
        title = re.sub(r"\s+", " ", str(row["title"] or row["website"] or msg["saved_link"])).strip()[:45]
        kind = "🎥" if row["media_type"] != "audio" else "🎵"
        quality = labels.get(str(row["quality"]), "") if row["quality"] else ""
        suffix = f" · {quality}" if quality else ""
        lines.append(f"{index + 1}. {html.escape(title)} {kind}{html.escape(suffix)}")
        keyboard.append([
            InlineKeyboardButton(f"▶️ {index + 1} {title}"[:60], callback_data=f"ux_load_{row['id']}"),
            InlineKeyboardButton("🗑️", callback_data=f"ux_fav_{row['id']}"),
        ])
    await target.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def library_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return
    bot_module = __import__("bot")
    if bot_module.is_banned(user.id):
        return
    await _library_screen(update.message, bot_module, user.id)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    bot_module = __import__("bot")
    if bot_module.is_banned(user.id):
        await query.answer()
        return
    data = query.data or ""
    language = _language(bot_module, user.id)
    msg = _MESSAGES[language]
    if data != "ux_favorite_current":
        await query.answer()

    if data in {"video_menu", "audio_menu"}:
        requested_type = "video" if data == "video_menu" else "audio"
        _, preferred_quality = _get_preferences(bot_module, user.id)
        if preferred_quality not in _ALLOWED[requested_type]:
            preferred_quality = _ALLOWED[requested_type][2]
        original_query = query

        class QueryProxy:
            def __init__(self):
                self.data = preferred_quality
                self.message = original_query.message
                self.from_user = original_query.from_user

            async def answer(self, *args, **kwargs):
                return None

            def __getattr__(self, name):
                return getattr(original_query, name)

        class UpdateProxy:
            callback_query = QueryProxy()

            def __getattr__(self, name):
                return getattr(update, name)

        await bot_module.download_media(UpdateProxy(), context)
        raise ApplicationHandlerStop

    if data == "ux_settings":
        await _settings_screen(query, bot_module, user.id)
        return
    if data == "ux_library":
        await _library_screen(query.message, bot_module, user.id)
        return
    if data in {"ux_set_video", "ux_set_audio"}:
        media_type = "video" if data == "ux_set_video" else "audio"
        _, current_quality = _get_preferences(bot_module, user.id)
        quality = current_quality if current_quality in _ALLOWED[media_type] else _ALLOWED[media_type][2]
        _set_preferences(bot_module, user.id, media_type, quality)
        await _settings_screen(query, bot_module, user.id)
        return
    if data == "ux_quality_menu":
        media_type, _ = _get_preferences(bot_module, user.id)
        await query.edit_message_text(msg["choose_quality"], parse_mode="HTML", reply_markup=_quality_keyboard(language, media_type))
        return
    if data.startswith("ux_quality_"):
        quality = data[len("ux_quality_"):]
        media_type = "audio" if quality.startswith("audio_") else "video"
        if quality not in _ALLOWED[media_type]:
            return
        _set_preferences(bot_module, user.id, media_type, quality)
        await _settings_screen(query, bot_module, user.id)
        return
    if data == "ux_favorite_current":
        url = context.user_data.get("video_url")
        info = context.user_data.get("sdc_info") or {}
        if not url:
            await query.answer(msg["no_url"], show_alert=True)
            return
        try:
            bot_module.validate_public_http_url(url)
        except Exception:
            await query.answer(msg["invalid_url"], show_alert=True)
            return

        media_type, quality = _get_preferences(bot_module, user.id)
        website = info.get("source") or bot_module.detect_website(url)
        title = info.get("title")

        conn = _db(bot_module)
        try:
            existing = conn.execute(
                "SELECT id FROM user_favorites WHERE user_id = ? AND url = ? ORDER BY id ASC LIMIT 1",
                (user.id, url),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE user_favorites
                       SET website = ?, media_type = ?, quality = ?, title = ?, created_at = ?
                       WHERE id = ? AND user_id = ?""",
                    (website, media_type, quality, title, _now(), existing["id"], user.id),
                )
            else:
                conn.execute(
                    """INSERT INTO user_favorites(user_id,url,website,media_type,quality,title,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (user.id, url, website, media_type, quality, title, _now()),
                )
            conn.commit()
        finally:
            conn.close()
        await query.answer(msg["save_alert"])
        return
    match = _FAV_RE.match(data)
    if match:
        favorite_id = int(match.group(1))
        conn = _db(bot_module)
        try:
            conn.execute("DELETE FROM user_favorites WHERE id = ? AND user_id = ?", (favorite_id, user.id))
            conn.commit()
        finally:
            conn.close()
        await _library_screen(query.message, bot_module, user.id)
        return
    match = _LOAD_RE.match(data)
    if match:
        favorite_id = int(match.group(1))
        conn = _db(bot_module)
        try:
            row = conn.execute(
                "SELECT url, media_type, quality FROM user_favorites WHERE id = ? AND user_id = ?",
                (favorite_id, user.id),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            await query.edit_message_text(msg["not_found"])
            return
        url = str(row["url"])
        try:
            bot_module.validate_public_http_url(url)
        except Exception:
            await query.edit_message_text(msg["saved_url_invalid"])
            return
        context.user_data["video_url"] = url
        saved_type = str(row["media_type"] or "")
        saved_quality = str(row["quality"] or "")
        if saved_type in _ALLOWED and saved_quality in _ALLOWED[saved_type]:
            context.user_data["ux_saved_preference"] = saved_quality
        else:
            context.user_data.pop("ux_saved_preference", None)
        await query.edit_message_text(
            msg["reload"],
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎥 " + msg["video"], callback_data="video_menu")],
                [InlineKeyboardButton("🎵 " + msg["audio"], callback_data="audio_menu")],
                [InlineKeyboardButton(msg["use_preferences"], callback_data="ux_use_preferences")],
            ]),
        )
        return
    if data == "ux_use_preferences":
        url = context.user_data.get("video_url")
        if not url:
            await query.edit_message_text(msg["ready"])
            return
        saved_quality = context.user_data.pop("ux_saved_preference", None)
        _, quality = _get_preferences(bot_module, user.id)
        quality = saved_quality or quality
        original_query = query

        class QueryProxy:
            def __init__(self):
                self.data = quality
                self.message = original_query.message
                self.from_user = original_query.from_user

            async def answer(self, *args, **kwargs):
                return None

            def __getattr__(self, name):
                return getattr(original_query, name)

        class UpdateProxy:
            callback_query = QueryProxy()

            def __getattr__(self, name):
                return getattr(update, name)

        await bot_module.download_media(UpdateProxy(), context)
        raise ApplicationHandlerStop


def register_user_experience_v2(app) -> None:
    if getattr(app, "_user_experience_v2_registered", False):
        return
    app._user_experience_v2_registered = True
    app.add_handler(CommandHandler("settings", settings_command), group=-3)
    app.add_handler(CommandHandler("library", library_command), group=-3)
    app.add_handler(
        CallbackQueryHandler(
            callback,
            pattern=r"^(?:video_menu|audio_menu|ux_(?:settings|library|set_video|set_audio|quality_menu|quality_(?:video_(?:best|1080|720|480|360)|audio_(?:best|320|256|192|128))|favorite_current|fav_\d+|load_\d+|use_preferences))$",
        ),
        group=-3,
    )
    print("🧩 User Experience v2: preferences + library enabled", flush=True)


__all__ = ["register_user_experience_v2"]