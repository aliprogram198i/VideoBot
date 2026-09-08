"""Additional read-only user intelligence for the admin dashboard.

This module intentionally uses unique callback names so it cannot collide with
legacy user-management ownership. It adds analytics and recent activity while
leaving existing ban/delete/history actions untouched.
"""

from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes


_PREFIX = "admin_users_plus_"


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _back(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 إحصائيات المستخدم", callback_data=f"{_PREFIX}stats_{user_id}")],
        [InlineKeyboardButton("🕘 آخر النشاطات", callback_data=f"{_PREFIX}activity_{user_id}")],
        [InlineKeyboardButton("👤 تفاصيل المستخدم", callback_data=f"admin_user_view_{user_id}")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
    ])


def _safe(value: Any, fallback: str = "غير متوفر") -> str:
    text = str(value).strip() if value is not None else ""
    return html.escape(text or fallback)


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    try:
        user_id = int((query.data or "").rsplit("_", 1)[1])
    except (ValueError, IndexError):
        return
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT user_id, username, first_name, downloads, first_seen, last_seen, is_banned "
            "FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) FROM downloads WHERE user_id = ?", (user_id,)).fetchone()[0]
        websites = conn.execute(
            "SELECT website, COUNT(*) AS count FROM downloads WHERE user_id = ? "
            "GROUP BY website ORDER BY count DESC LIMIT 5", (user_id,)
        ).fetchall()
        media = conn.execute(
            "SELECT media_type, COUNT(*) AS count FROM downloads WHERE user_id = ? "
            "GROUP BY media_type ORDER BY count DESC", (user_id,)
        ).fetchall()
    finally:
        conn.close()
    if not user:
        await query.edit_message_text("❌ المستخدم غير موجود.")
        return
    lines = ["📊 <b>تحليلات المستخدم</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    lines += [
        f"🆔 ID: <code>{user_id}</code>",
        f"👤 الحساب: {_safe(user['username'], 'بدون username')}",
        f"📥 العداد: {int(user['downloads'] or 0)}",
        f"🧾 السجل الفعلي: {int(total)} عملية",
        f"📅 أول ظهور: {_safe(user['first_seen'])}",
        f"🕒 آخر نشاط: {_safe(user['last_seen'])}",
        f"🚦 الحالة: {'🚫 محظور' if user['is_banned'] else '🟢 نشط'}",
        "",
        "🌐 <b>أكثر المنصات استخدامًا</b>",
    ]
    lines.extend(f"• {_safe(row['website'])}: {int(row['count'])}" for row in websites) or lines.append("• لا توجد بيانات")
    lines += ["", "🎬 <b>أنواع الوسائط</b>"]
    lines.extend(f"• {_safe(row['media_type'])}: {int(row['count'])}" for row in media) or lines.append("• لا توجد بيانات")
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=_back(user_id))


async def activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    try:
        user_id = int((query.data or "").rsplit("_", 1)[1])
    except (ValueError, IndexError):
        return
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT website, media_type, quality, url, created_at FROM downloads "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,)
        ).fetchall()
    finally:
        conn.close()
    lines = ["🕘 <b>آخر نشاطات المستخدم</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not rows:
        lines.append("لا توجد عمليات تنزيل مسجلة.")
    else:
        for i, row in enumerate(rows, 1):
            url = _safe(row['url'], '')
            if len(url) > 70:
                url = url[:67] + "..."
            lines.append(
                f"{i}. {_safe(row['website'])} • {_safe(row['media_type'])} • "
                f"{_safe(row['quality'])} • {_safe(row['created_at'])}\n   🔗 {url}"
            )
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=_back(user_id))


def register_admin_users_plus(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(CallbackQueryHandler(
        lambda u, c: stats_callback(u, c, get_db, owner_id),
        pattern=r"^admin_users_plus_stats_\d+$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: activity_callback(u, c, get_db, owner_id),
        pattern=r"^admin_users_plus_activity_\d+$",
    ), group=-1)
