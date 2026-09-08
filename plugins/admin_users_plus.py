"""Enhanced admin users workspace: filters, summaries and user analytics."""

from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from .admin_common import authorize

_PREFIX = "admin_users_plus_"
_PAGE_SIZE = 8


def _authorized(update: Update, get_db, owner_id: int) -> bool:
    return authorize(update, get_db, owner_id, "users.view")


def _safe(value: Any, fallback: str = "غير متوفر") -> str:
    text = str(value).strip() if value is not None else ""
    return html.escape(text or fallback)


def _workspace_keyboard(offset: int, has_next: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔎 بحث متقدم", callback_data="admin_search")],
        [InlineKeyboardButton("🟢 النشطون", callback_data=f"{_PREFIX}filter_active_0"), InlineKeyboardButton("🚫 المحظورون", callback_data=f"{_PREFIX}filter_banned_0")],
        [InlineKeyboardButton("🔥 الأكثر تنزيلًا", callback_data=f"{_PREFIX}filter_top_0"), InlineKeyboardButton("🆕 الأحدث", callback_data=f"{_PREFIX}filter_new_0")],
    ]
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"{_PREFIX}all_{max(0, offset - _PAGE_SIZE)}"))
    if has_next:
        nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"{_PREFIX}all_{offset + _PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)


def _user_buttons(rows) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(f"👤 {i}. { _safe(row['username'], 'بدون معرف')[:28] }", callback_data=f"admin_user_view_{int(row['user_id'])}")] for i, row in enumerate(rows, 1)]


async def _render_list(update: Update, get_db, owner_id: int, mode: str = "all", offset: int = 0) -> None:
    query = update.callback_query
    if not _authorized(update, get_db, owner_id):
        return
    conn = get_db()
    try:
        if mode == "active":
            where, params, label = "WHERE is_banned = 0", (), "🟢 النشطون"
            order = "last_seen DESC, user_id DESC"
        elif mode == "banned":
            where, params, label = "WHERE is_banned = 1", (), "🚫 المحظورون"
            order = "last_seen DESC, user_id DESC"
        elif mode == "top":
            where, params, label = "", (), "🔥 الأكثر تنزيلًا"
            order = "downloads DESC, last_seen DESC, user_id DESC"
        elif mode == "new":
            where, params, label = "", (), "🆕 الأحدث"
            order = "first_seen DESC, user_id DESC"
        else:
            where, params, label = "", (), "👥 جميع المستخدمين"
            order = "last_seen DESC, user_id DESC"
        total = conn.execute(f"SELECT COUNT(*) FROM users {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT user_id, username, first_name, last_name, downloads, last_seen, is_banned FROM users {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (*params, _PAGE_SIZE + 1, offset),
        ).fetchall()
        summary = conn.execute("SELECT COUNT(*) AS total, COALESCE(SUM(is_banned),0) AS banned, COALESCE(SUM(downloads),0) AS downloads FROM users").fetchone()
    finally:
        conn.close()
    has_next = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]
    lines = [f"{label} <b>— مركز المستخدمين</b>", "━━━━━━━━━━━━━━━━━━━━", "", f"👥 الإجمالي: {int(summary['total'])}", f"🟢 نشط: {int(summary['total']) - int(summary['banned'])}", f"🚫 محظور: {int(summary['banned'])}", f"📥 إجمالي التحميلات: {int(summary['downloads'])}", f"📄 النتائج: {int(offset)+1 if rows else 0}–{int(offset)+len(rows)} من {int(total)}", ""]
    if rows:
        for i, row in enumerate(rows, offset + 1):
            name = row['username'] or " ".join(str(x).strip() for x in (row['first_name'], row['last_name']) if x) or str(row['user_id'])
            status = "🚫" if row['is_banned'] else "🟢"
            lines.append(f"{i}. {status} {_safe(name)[:35]} • 📥 {int(row['downloads'] or 0)} • 🕒 {_safe(row['last_seen'], '—')[:25]}")
    else:
        lines.append("لا توجد نتائج.")
    keyboard = _user_buttons(rows)
    base = _workspace_keyboard(offset, has_next).inline_keyboard
    keyboard.extend([list(r) for r in base])
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def workspace_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data == "admin_users_page_0":
        await _render_list(update, get_db, owner_id, "all", 0)
        return
    try:
        mode, raw = data.removeprefix(_PREFIX).rsplit("_", 1)
        offset = int(raw)
    except (ValueError, IndexError):
        return
    if mode in {"all", "filter_active", "filter_banned", "filter_top", "filter_new"}:
        mode_map = {"all": "all", "filter_active": "active", "filter_banned": "banned", "filter_top": "top", "filter_new": "new"}
        await _render_list(update, get_db, owner_id, mode_map[mode], max(0, offset))


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id): return
    try: user_id = int((query.data or "").rsplit("_", 1)[1])
    except (ValueError, IndexError): return
    conn = get_db()
    try:
        user = conn.execute("SELECT user_id, username, first_name, downloads, first_seen, last_seen, is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
        total = conn.execute("SELECT COUNT(*) FROM downloads WHERE user_id = ?", (user_id,)).fetchone()[0]
        websites = conn.execute("SELECT website, COUNT(*) AS count FROM downloads WHERE user_id = ? GROUP BY website ORDER BY count DESC LIMIT 5", (user_id,)).fetchall()
        media = conn.execute("SELECT media_type, COUNT(*) AS count FROM downloads WHERE user_id = ? GROUP BY media_type ORDER BY count DESC", (user_id,)).fetchall()
    finally: conn.close()
    if not user: await query.edit_message_text("❌ المستخدم غير موجود."); return
    lines = ["📊 <b>تحليلات المستخدم</b>", "━━━━━━━━━━━━━━━━━━━━", "", f"🆔 ID: <code>{user_id}</code>", f"👤 الحساب: {_safe(user['username'], 'بدون username')}", f"📥 العداد: {int(user['downloads'] or 0)}", f"🧾 السجل الفعلي: {int(total)} عملية", f"📅 أول ظهور: {_safe(user['first_seen'])}", f"🕒 آخر نشاط: {_safe(user['last_seen'])}", f"🚦 الحالة: {'🚫 محظور' if user['is_banned'] else '🟢 نشط'}", "", "🌐 <b>أكثر المنصات استخدامًا</b>"]
    lines.extend(f"• {_safe(r['website'])}: {int(r['count'])}" for r in websites) or lines.append("• لا توجد بيانات")
    lines += ["", "🎬 <b>أنواع الوسائط</b>"]
    lines.extend(f"• {_safe(r['media_type'])}: {int(r['count'])}" for r in media) or lines.append("• لا توجد بيانات")
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🕘 آخر النشاطات", callback_data=f"{_PREFIX}activity_{user_id}")], [InlineKeyboardButton("👤 تفاصيل المستخدم", callback_data=f"admin_user_view_{user_id}")], [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")]]))


async def activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id): return
    try: user_id = int((query.data or "").rsplit("_", 1)[1])
    except (ValueError, IndexError): return
    conn = get_db()
    try: rows = conn.execute("SELECT website, media_type, quality, url, created_at FROM downloads WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()
    finally: conn.close()
    lines = ["🕘 <b>آخر نشاطات المستخدم</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not rows: lines.append("لا توجد عمليات تنزيل مسجلة.")
    else:
        for i, r in enumerate(rows, 1): lines.append(f"{i}. {_safe(r['website'])} • {_safe(r['media_type'])} • {_safe(r['quality'])} • {_safe(r['created_at'])}")
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 الإحصائيات", callback_data=f"{_PREFIX}stats_{user_id}")], [InlineKeyboardButton("👤 تفاصيل المستخدم", callback_data=f"admin_user_view_{user_id}"),], [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")]]))


def register_admin_users_plus(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: workspace_callback(u, c, get_db, owner_id), pattern=r"^(?:admin_users_page_0|admin_users_plus_(?:all|filter_active|filter_banned|filter_top|filter_new)_\d+)$"), group=-2)
    app.add_handler(CallbackQueryHandler(lambda u, c: stats_callback(u, c, get_db, owner_id), pattern=r"^admin_users_plus_stats_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: activity_callback(u, c, get_db, owner_id), pattern=r"^admin_users_plus_activity_\d+$"), group=-1)
