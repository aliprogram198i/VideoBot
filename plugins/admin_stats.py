"""Isolated administrative statistics and dashboard callbacks."""

from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _stats(get_db) -> dict[str, Any]:
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS users, SUM(CASE WHEN is_banned = 1 THEN 1 ELSE 0 END) AS banned FROM users").fetchone()
        downloads = conn.execute("SELECT COUNT(*) AS count FROM downloads").fetchone()["count"]
        videos = conn.execute("SELECT COUNT(*) AS count FROM downloads WHERE media_type = 'video'").fetchone()["count"]
        audio = conn.execute("SELECT COUNT(*) AS count FROM downloads WHERE media_type = 'audio'").fetchone()["count"]
        phones = conn.execute("SELECT COUNT(*) AS count FROM users WHERE phone IS NOT NULL AND phone != ''").fetchone()["count"]
        locations = conn.execute("SELECT COUNT(*) AS count FROM users WHERE latitude IS NOT NULL AND longitude IS NOT NULL").fetchone()["count"]
        websites = conn.execute("SELECT website, COUNT(*) AS count FROM downloads GROUP BY website ORDER BY count DESC LIMIT 10").fetchall()
        languages = conn.execute("SELECT language, COUNT(*) AS count FROM users GROUP BY language ORDER BY count DESC").fetchall()
        genders = conn.execute("SELECT gender, COUNT(*) AS count FROM users WHERE gender IS NOT NULL AND gender != '' GROUP BY gender").fetchall()
        return {"users": int(row["users"] or 0), "banned": int(row["banned"] or 0), "downloads": int(downloads or 0), "videos": int(videos or 0), "audio": int(audio or 0), "phones": int(phones or 0), "locations": int(locations or 0), "websites": websites, "languages": languages, "genders": genders}
    finally:
        conn.close()


def _keyboard(days: int = 30) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1 يوم", callback_data="admin_dashboard_1"), InlineKeyboardButton("7 أيام", callback_data="admin_dashboard_7"), InlineKeyboardButton("30 يوم", callback_data="admin_dashboard_30")],
        [InlineKeyboardButton("📥 آخر التحميلات", callback_data="admin_recent_downloads")],
        [InlineKeyboardButton("👥 أكثر المستخدمين", callback_data="admin_top_users"), InlineKeyboardButton("🌐 أكثر المنصات", callback_data="admin_top_websites")],
        [InlineKeyboardButton("📢 الإذاعة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
    ])


def _render(data: dict[str, Any], days: int) -> str:
    lines = ["📊 <b>لوحة المعلومات والتحليلات</b>", "━━━━━━━━━━━━━━━━━━━━", f"🗓 الفترة: آخر {days} يوم", "", f"👥 المستخدمون: {data['users']}", f"🚫 المحظورون: {data['banned']}", f"📥 التحميلات: {data['downloads']}", f"🎥 الفيديوهات: {data['videos']}", f"🎵 الصوتيات: {data['audio']}", f"📱 أرقام الهواتف: {data['phones']}", f"📍 المواقع: {data['locations']}", "", "🌐 <b>أكثر المنصات</b>"]
    for row in data["websites"][:10]:
        lines.append(f"• {html.escape(str(row['website'] or 'غير معروف'))}: {int(row['count'])}")
    lines.append("\n🌍 <b>اللغات</b>")
    names = {"ar": "🇸🇦 العربية", "en": "🇬🇧 English", "tr": "🇹🇷 Türkçe", "de": "🇩🇪 Deutsch"}
    for row in data["languages"]:
        lines.append(f"• {names.get(row['language'], row['language'])}: {int(row['count'])}")
    return "\n".join(lines)[:3900]


async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    try:
        days = int((query.data or "").rsplit("_", 1)[-1])
    except ValueError:
        days = 30
    if days not in {1, 7, 30}:
        days = 30
    await query.edit_message_text(_render(_stats(get_db), days), parse_mode="HTML", reply_markup=_keyboard(days))


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    data = _stats(get_db)
    lines = ["📈 <b>الإحصائيات</b>", "━━━━━━━━━━━━━━━━━━━━", "", f"👥 المستخدمون: {data['users']}", f"🚫 المحظورون: {data['banned']}", f"📥 التحميلات: {data['downloads']}", f"🎥 الفيديوهات: {data['videos']}", f"🎵 الصوتيات: {data['audio']}", f"📱 الهواتف: {data['phones']}", f"📍 المواقع: {data['locations']}", "", "🚻 <b>الجنس</b>"]
    lines += [f"• {html.escape(str(r['gender']))}: {int(r['count'])}" for r in data['genders']]
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 لوحة المعلومات", callback_data="admin_dashboard_30")], [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")]]))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    if not _authorized(update, owner_id) or not update.message:
        return
    data = _stats(get_db)
    text = f"📊 <b>إحصائيات البوت</b>\n━━━━━━━━━━━━━━━━━━━━\n\n👥 المستخدمون: {data['users']}\n🚫 المحظورون: {data['banned']}\n📥 التحميلات: {data['downloads']}\n🎥 الفيديوهات: {data['videos']}\n🎵 الصوتيات: {data['audio']}\n📱 الهواتف: {data['phones']}\n📍 المواقع: {data['locations']}"
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=_keyboard())


async def recent_downloads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    conn = get_db()
    try:
        rows = conn.execute("SELECT user_id, website, media_type, quality, created_at FROM downloads ORDER BY id DESC LIMIT 15").fetchall()
    finally:
        conn.close()
    lines = ["📥 <b>آخر التحميلات</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not rows:
        lines.append("لا توجد تحميلات مسجلة.")
    for row in rows:
        media = "🎥" if row["media_type"] == "video" else "🎵"
        lines.append(f"• {media} {html.escape(str(row['website'] or ''))} | {html.escape(str(row['quality'] or ''))} | {row['user_id']} | {html.escape(str(row['created_at'] or ''))}")
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data="admin_recent_downloads")], [InlineKeyboardButton("📊 لوحة المعلومات", callback_data="admin_dashboard_30")]]))


async def top_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    conn = get_db()
    try:
        rows = conn.execute("SELECT user_id, username, first_name, downloads FROM users ORDER BY downloads DESC, last_seen DESC LIMIT 15").fetchall()
    finally:
        conn.close()
    lines = ["👥 <b>أكثر المستخدمين نشاطاً</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    for i, row in enumerate(rows, 1):
        name = f"@{row['username']}" if row['username'] else (row['first_name'] or f"ID {row['user_id']}")
        lines.append(f"{i}. {html.escape(str(name))} — 📥 {int(row['downloads'] or 0)}")
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 لوحة المعلومات", callback_data="admin_dashboard_30")]]))


async def top_websites_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    conn = get_db()
    try:
        rows = conn.execute("SELECT website, COUNT(*) AS count FROM downloads GROUP BY website ORDER BY count DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    total = sum(int(r['count']) for r in rows) or 1
    lines = ["🌐 <b>أكثر المنصات استخداماً</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    for i, row in enumerate(rows, 1):
        pct = int(row['count']) * 100 / total
        lines.append(f"{i}. {html.escape(str(row['website'] or 'غير معروف'))} — {int(row['count'])} ({pct:.1f}%)")
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 لوحة المعلومات", callback_data="admin_dashboard_30")]]))


def register_admin_stats(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(CommandHandler("stats", lambda u, c: stats_command(u, c, get_db, owner_id)), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: dashboard_callback(u, c, get_db, owner_id), pattern=r"^admin_dashboard_(1|7|30)$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: stats_callback(u, c, get_db, owner_id), pattern=r"^admin_stats$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: recent_downloads_callback(u, c, get_db, owner_id), pattern=r"^admin_recent_downloads$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: top_users_callback(u, c, get_db, owner_id), pattern=r"^admin_top_users$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: top_websites_callback(u, c, get_db, owner_id), pattern=r"^admin_top_websites$"), group=-1)
