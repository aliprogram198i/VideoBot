"""Admin-only per-user download-history management.

This module is intentionally isolated from the legacy admin dashboard. It
keeps user records intact and only clears rows belonging to the selected user
from the downloads history, while resetting the denormalized user download
counter to zero. Every destructive action is confirmed and audited.
"""

from __future__ import annotations

import html
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes


_USERS_RE = re.compile(r"^admin_users_page_(\d+)$")
_USER_RE = re.compile(r"^admin_user_view_(\d+)$")
_CLEAR_RE = re.compile(r"^admin_user_clear_(\d+)$")
_CONFIRM_RE = re.compile(r"^admin_user_clear_confirm_(\d+)$")
_CANCEL_RE = re.compile(r"^admin_user_clear_cancel_(\d+)$")
_PAGE_SIZE = 10


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _display_name(row: Any) -> str:
    username = row["username"] if row["username"] else None
    if username:
        return f"@{html.escape(str(username))}"
    parts = [row["first_name"], row["last_name"]]
    name = " ".join(str(part).strip() for part in parts if part)
    return html.escape(name[:40] if name else f"ID {row['user_id']}")


def _users_keyboard(offset: int, has_next: bool) -> InlineKeyboardMarkup:
    rows = []
    if offset > 0:
        rows.append([InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_users_page_{max(0, offset - _PAGE_SIZE)}")])
    if has_next:
        rows.append([InlineKeyboardButton("➡️ التالي", callback_data=f"admin_users_page_{offset + _PAGE_SIZE}")])
    rows.append([InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")])
    return InlineKeyboardMarkup(rows)


def _user_detail_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 مسح سجل التحميلات", callback_data=f"admin_user_clear_{user_id}")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
    ])


def register_admin_user_history(app: Any, get_db, owner_id: int) -> None:
    """Register isolated user-history management callbacks with admin priority."""
    app.add_handler(CallbackQueryHandler(
        lambda u, c: users_callback(u, c, get_db, owner_id),
        pattern=r"^admin_users_page_\d+$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: user_view_callback(u, c, get_db, owner_id),
        pattern=r"^admin_user_view_\d+$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: clear_prompt_callback(u, c, get_db, owner_id),
        pattern=r"^admin_user_clear_\d+$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: clear_confirm_callback(u, c, get_db, owner_id),
        pattern=r"^admin_user_clear_confirm_\d+$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: clear_cancel_callback(u, c, get_db, owner_id),
        pattern=r"^admin_user_clear_cancel_\d+$",
    ), group=-1)


async def users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return

    match = _USERS_RE.match(query.data or "")
    offset = int(match.group(1)) if match else 0
    offset = max(0, offset)

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT user_id, username, first_name, last_name, downloads, last_seen, is_banned "
            "FROM users ORDER BY last_seen DESC, user_id DESC LIMIT ? OFFSET ?",
            (_PAGE_SIZE + 1, offset),
        ).fetchall()
    finally:
        conn.close()

    has_next = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]
    lines = ["👥 <b>المستخدمون</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    keyboard = []

    if not rows:
        lines.append("لا يوجد مستخدمون في هذه الصفحة.")
    else:
        for index, row in enumerate(rows, offset + 1):
            status = "🚫" if row["is_banned"] else "🟢"
            lines.append(
                f"{index}. {status} {_display_name(row)} — "
                f"{int(row['downloads'] or 0)} تحميل"
            )
            keyboard.append([
                InlineKeyboardButton(
                    f"👤 {index} • {str(row['user_id'])}",
                    callback_data=f"admin_user_view_{int(row['user_id'])}",
                )
            ])

    navigation = _users_keyboard(offset, has_next)
    keyboard.extend([list(row) for row in navigation.inline_keyboard])
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    _audit(get_db, owner_id, "view_admin_users", details=f"offset={offset}")


async def user_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return

    match = _USER_RE.match(query.data or "")
    if not match:
        return
    user_id = int(match.group(1))

    conn = get_db()
    try:
        user = conn.execute(
            "SELECT user_id, username, first_name, last_name, downloads, language, "
            "country, last_seen, is_banned FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            await query.edit_message_text(
                "❌ المستخدم غير موجود.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")]]),
            )
            return
        history_count = conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    username = f"@{html.escape(str(user['username']))}" if user["username"] else "غير متوفر"
    name = html.escape(" ".join(str(p).strip() for p in (user["first_name"], user["last_name"]) if p)[:80] or "غير متوفر")
    text = (
        "👤 <b>تفاصيل المستخدم</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 الاسم: {name}\n"
        f"🔗 المعرف: {username}\n"
        f"🌐 اللغة: {html.escape(str(user['language'] or 'غير محددة'))}\n"
        f"📍 البلد: {html.escape(str(user['country'] or 'غير محدد'))}\n"
        f"📥 العداد: {int(user['downloads'] or 0)}\n"
        f"🧾 السجل الفعلي: {int(history_count)} عملية\n"
        f"🕒 آخر ظهور: {html.escape(str(user['last_seen'] or 'غير متوفر'))}\n"
        f"🚦 الحالة: {'🚫 محظور' if user['is_banned'] else '🟢 نشط'}\n\n"
        "مسح السجل لا يحذف المستخدم أو حسابه أو إعداداته."
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=_user_detail_keyboard(user_id))
    _audit(get_db, owner_id, "view_admin_user", target_id=user_id)


async def clear_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    match = _CLEAR_RE.match(query.data or "")
    if not match:
        return
    user_id = int(match.group(1))

    conn = get_db()
    try:
        user = conn.execute(
            "SELECT user_id, username, first_name, last_name FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        history_count = conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    if not user:
        await query.edit_message_text("❌ المستخدم غير موجود.")
        return

    name = _display_name(user)
    text = (
        "⚠️ <b>تأكيد مسح السجل</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 المستخدم: {name}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🧾 العمليات التي ستُمسح: <b>{int(history_count)}</b>\n\n"
        "سيتم حذف سجل التحميلات لهذا المستخدم فقط، وتصفير عداد تحميلاته.\n"
        "<b>لن يتم حذف المستخدم أو بيانات حسابه.</b>\n\n"
        "هل تريد المتابعة؟"
    )
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ نعم، امسح السجل", callback_data=f"admin_user_clear_confirm_{user_id}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_user_clear_cancel_{user_id}")],
        ]),
    )


async def clear_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    if not _authorized(update, owner_id):
        await query.answer()
        return
    match = _CANCEL_RE.match(query.data or "")
    if not match:
        await query.answer()
        return
    await query.answer("تم الإلغاء")
    await user_view_callback(update, context, get_db, owner_id)


async def clear_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    match = _CONFIRM_RE.match(query.data or "")
    if not match:
        return
    user_id = int(match.group(1))

    conn = get_db()
    deleted = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not exists:
            conn.rollback()
            await query.edit_message_text("❌ المستخدم غير موجود.")
            return
        deleted = conn.execute("DELETE FROM downloads WHERE user_id = ?", (user_id,)).rowcount
        conn.execute("UPDATE users SET downloads = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    _audit(
        get_db,
        owner_id,
        "clear_user_download_history",
        target_id=user_id,
        details=f"deleted_download_rows={int(deleted or 0)}",
    )
    await query.edit_message_text(
        "✅ <b>تم مسح سجل المستخدم</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 المستخدم: <code>{user_id}</code>\n"
        f"🗑️ العمليات المحذوفة: {int(deleted or 0)}\n"
        "📊 عداد التحميلات: 0\n\n"
        "👤 حساب المستخدم وبياناته الأساسية لم تُحذف.",
        parse_mode="HTML",
        reply_markup=_user_detail_keyboard(user_id),
    )


def _audit(get_db, admin_id: int, action: str, target_id: int | None = None, details: str | None = None) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO admin_audit_logs (admin_id, action, target_id, details, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (admin_id, action, target_id, details),
        )
        conn.commit()
    finally:
        conn.close()
