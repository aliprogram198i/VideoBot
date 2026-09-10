"""Phase-3 security center: read-only security posture view."""
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from .admin_authorization import authorize


async def _callback(update, context, admin_id: int) -> None:
    query = update.callback_query
    if not authorize(update, admin_id):
        await query.answer()
        return
    text = (
        "🛡️ <b>مركز الأمان</b>\n\n"
        "🔐 المصادقة: محمية بمعرّف المسؤول الحالي\n"
        "🧩 إدارة الصلاحيات: تعتمد على طبقة الإدارة المعزولة\n"
        "🧾 التدقيق: متاح عبر مركز التدقيق\n"
        "⚠️ العمليات الحساسة: لا توجد عملية تغيير أو حذف مفعّلة من هذه الشاشة\n\n"
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


def register_admin_security_center(app: Any, admin_id: int) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: _callback(u, c, admin_id), pattern=r"^admin_security$"), group=-150)
