"""Canonical six-domain admin command center.

The command center is a navigation layer only. Existing feature handlers remain
the source of truth, so this change does not duplicate business logic or alter
download behavior.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop

from .admin_common import authorize
from .admin_control_center import _home_text, admin_keyboard, audit


def command_center_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0"),
            InlineKeyboardButton("📥 التنزيلات", callback_data="admin_records"),
        ],
        [
            InlineKeyboardButton("🚨 المراقبة والحوادث", callback_data="admin_ops_dashboard"),
            InlineKeyboardButton("⚙️ النظام والعمليات", callback_data="admin_health"),
        ],
        [
            InlineKeyboardButton("📢 التواصل", callback_data="admin_broadcast"),
            InlineKeyboardButton("🛡️ الأمان والإدارة", callback_data="admin_roles"),
        ],
        [InlineKeyboardButton("➕ المزيد من الأدوات", callback_data="admin_more")],
    ])


async def command_center_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    audit(get_db, int(update.effective_user.id), "open_command_center")
    await query.edit_message_text(
        _home_text(get_db),
        parse_mode="HTML",
        reply_markup=command_center_keyboard(),
    )
    raise ApplicationHandlerStop


async def more_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    audit(get_db, int(update.effective_user.id), "open_admin_tools")
    await query.edit_message_text(
        "🧰 <b>الأدوات الإدارية الكاملة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "الأدوات المتقدمة متاحة هنا دون إزالة أي وظيفة موجودة.",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )
    raise ApplicationHandlerStop


def register_admin_command_center(app, get_db, owner_id: int) -> None:
    app.add_handler(
        __import__("telegram.ext", fromlist=["CallbackQueryHandler"]).CallbackQueryHandler(
            lambda u, c: command_center_callback(u, c, get_db, owner_id),
            pattern=r"^admin_home$|^admin_control_center$",
        ),
        group=-300,
    )
    app.add_handler(
        __import__("telegram.ext", fromlist=["CallbackQueryHandler"]).CallbackQueryHandler(
            lambda u, c: more_callback(u, c, get_db, owner_id),
            pattern=r"^admin_more$",
        ),
        group=-300,
    )
