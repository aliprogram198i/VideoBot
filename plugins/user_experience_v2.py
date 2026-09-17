"""User Experience v2: per-user preferences and a personal favorites library."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

_ALLOWED = {
    "video": ("video_best", "video_1080", "video_720", "video_480", "video_360"),
    "audio": ("audio_best", "audio_320", "audio_256", "audio_192", "audio_128"),
}
_LABELS = {
    "video_best": "أفضل جودة",
    "video_1080": "1080p",
    "video_720": "720p",
    "video_480": "480p",
    "video_360": "360p",
    "audio_best": "أفضل جودة",
    "audio_320": "320 kbps",
    "audio_256": "256 kbps",
    "audio_192": "192 kbps",
    "audio_128": "128 kbps",
}
_FAV_RE = re.compile(r"^ux_fav_(\d+)$")
_LOAD_RE = re.compile(r"^ux_load_(\d+)$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def _settings_keyboard(media_type: str, quality: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 فيديو" + (" ✅" if media_type == "video" else ""), callback_data="ux_set_video")],
        [InlineKeyboardButton("🎵 صوت" + (" ✅" if media_type == "audio" else ""), callback_data="ux_set_audio")],
        [InlineKeyboardButton(f"⚙️ الجودة الحالية: {_LABELS.get(quality, 'غير محددة')}", callback_data="ux_quality_menu")],
        [InlineKeyboardButton("📚 مكتبتي", callback_data="ux_library")],
    ])


def _quality_keyboard(media_type: str) -> InlineKeyboardMarkup:
    labels = [(value, _LABELS[value]) for value in _ALLOWED[media_type]]
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"ux_quality_{value}")] for value, label in labels] + [[InlineKeyboardButton("🔙 الإعدادات", callback_data="ux_settings")]])


async def _settings_screen(target, bot_module: Any, user_id: int) -> None:
    media_type, quality = _get_preferences(bot_module, user_id)
    text = (
        "⚙️ <b>إعدادات التحميل</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🎛️ النوع: <b>{'فيديو' if media_type == 'video' else 'صوت'}</b>\n"
        f"📐 الجودة: <b>{html.escape(_LABELS.get(quality, 'غير محددة'))}</b>\n\n"
        "سيحتفظ AliBot بهذه الإعدادات لهذا المستخدم."
    )
    keyboard = _settings_keyboard(media_type, quality)
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
        await target.reply_text("📚 <b>مكتبتي</b>\n\nلا توجد عناصر محفوظة بعد.", parse_mode="HTML")
        return
    lines = ["📚 <b>مكتبتي</b>", "━━━━━━━━━━━━━━━━━━", ""]
    keyboard = []
    for index, row in enumerate(rows):
        title = re.sub(r"\s+", " ", str(row["title"] or row["website"] or "رابط محفوظ")).strip()[:45]
        kind = "🎥" if row["media_type"] != "audio" else "🎵"
        quality = _LABELS.get(str(row["quality"]), "") if row["quality"] else ""
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
    await query.answer()
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
        await query.edit_message_text("📐 <b>اختر الجودة الافتراضية</b>", parse_mode="HTML", reply_markup=_quality_keyboard(media_type))
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
            await query.answer("لا يوجد رابط لحفظه.", show_alert=True)
            return
        try:
            bot_module.validate_public_http_url(url)
        except Exception:
            await query.answer("تعذر التحقق من الرابط.", show_alert=True)
            return
        conn = _db(bot_module)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO user_favorites(user_id,url,website,media_type,quality,title,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (user.id, url, info.get("source"), None, None, info.get("title"), _now()),
            )
            conn.commit()
        finally:
            conn.close()
        await query.answer("⭐ تم الحفظ في مكتبتك.")
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
            await query.edit_message_text("❌ العنصر غير موجود.")
            return
        url = str(row["url"])
        try:
            bot_module.validate_public_http_url(url)
        except Exception:
            await query.edit_message_text("❌ تعذر التحقق من الرابط المحفوظ.")
            return
        context.user_data["video_url"] = url
        saved_type = str(row["media_type"] or "")
        saved_quality = str(row["quality"] or "")
        if saved_type in _ALLOWED and saved_quality in _ALLOWED[saved_type]:
            context.user_data["ux_saved_preference"] = saved_quality
        else:
            context.user_data.pop("ux_saved_preference", None)
        await query.edit_message_text(
            "🔁 <b>إعادة تحميل من المكتبة</b>\n\nاختر النوع:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎥 فيديو", callback_data="video_menu")],
                [InlineKeyboardButton("🎵 صوت", callback_data="audio_menu")],
                [InlineKeyboardButton("⚙️ استخدام الإعدادات", callback_data="ux_use_preferences")],
            ]),
        )
        return
    if data == "ux_use_preferences":
        url = context.user_data.get("video_url")
        if not url:
            await query.edit_message_text("❌ لا يوجد رابط جاهز.")
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


def register_user_experience_v2(app) -> None:
    if getattr(app, "_user_experience_v2_registered", False):
        return
    app._user_experience_v2_registered = True
    app.add_handler(CommandHandler("settings", settings_command), group=-3)
    app.add_handler(CommandHandler("library", library_command), group=-3)
    app.add_handler(
        CallbackQueryHandler(
            callback,
            pattern=r"^ux_(?:settings|library|set_video|set_audio|quality_menu|quality_(?:best|1080|720|480|360|320|256|192|128)|favorite_current|fav_\d+|load_\d+|use_preferences)$",
        ),
        group=-3,
    )
    print("🧩 User Experience v2: preferences + library enabled", flush=True)


__all__ = ["register_user_experience_v2"]
