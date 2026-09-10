"""Phase-3 security center: read-only security posture view."""
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from .admin_common import authorize


async def _callback(update, context, admin_id: int, get_db) -> None:
    query = update.callback_query
    if not authorize(update, get_db, admin_id, "security.view"):
        await query.answer()
        return
    text = (
        "🛡️ <b>مركز الأمان</b>\n\n"
        "🔐 الوصول: فحص صلاحيات مركزي وبوضع fail-closed\n"
        "🧩 الصلاحيات: مرتبطة بالأدوار الحالية\n"
        "🧾 التدقيق: متاح عبر سجل التدقيق\n"
        "⚠️ العمليات الحساسة: لا توجد تغييرات مفعّلة من هذه الشاشة\n\n"
        "الحالة: <b>وضع آمن للقراءة فقط</b>"
    )
    await query.answer()
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ الأدوار والصلاحيات", callback_data="admin_roles")],
        [InlineKeyboardButton("🧾 سجل التدقيق", callback_data="admin_audit")],
        [InlineKeyboardButton("🗄️ النسخ والاستعادة", callback_data="admin_backup_recovery")],
        [InlineKeyboardButton("↩️ مركز العمليات", callback_data="admin_ops_dashboard")],
    ]))
    raise ApplicationHandlerStop


def register_admin_security_center(app: Any, admin_id: int, get_db) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: _callback(u, c, admin_id, get_db), pattern=r"^admin_security$"), group=-150)
