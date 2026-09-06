"""Isolated administration layer for AliBot.

This module is the bootstrap boundary for the admin UI. It does not own
 downloader logic or user state; it only connects the existing admin
 control-center callbacks to the running Telegram application and provides
 a safe /hebaali entry route.
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CommandHandler, ContextTypes

from .admin_control_center import register_admin_control_center


def _admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🩺 صحة النظام", callback_data="admin_health")],
        [InlineKeyboardButton("🧾 سجل التدقيق", callback_data="admin_audit")],
        [InlineKeyboardButton("🛡️ الأدوار والصلاحيات", callback_data="admin_roles")],
        [InlineKeyboardButton("📊 لوحة الإحصائيات", callback_data="admin_dashboard_30")],
        [InlineKeyboardButton("🤖 Smart Operations", callback_data="admin_smart_operations")],
    ])


async def _admin_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    bot_module: Any,
    admin_id: int,
) -> None:
    """Open the isolated admin control center for the owner only."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message or user.id != admin_id:
        return

    await message.reply_text(
        "🎛️ <b>مركز التحكم الإداري</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 النظام الإداري يعمل\n"
        "🔐 الوصول محمي بمالك البوت\n"
        "🧾 التدقيق الإداري مفعّل\n"
        "🛡️ نظام الأدوار جاهز للتوسع\n\n"
        "اختر القسم المطلوب:",
        parse_mode="HTML",
        reply_markup=_admin_keyboard(),
    )
    raise ApplicationHandlerStop


def register_admin_layer(app: Any, bot_module: Any, admin_id: int) -> None:
    """Register only the admin components not already owned by bot.py."""
    register_admin_control_center(app, bot_module.get_db, admin_id)

    # Group -1 gives the owner route priority over the legacy command router.
    app.add_handler(
        CommandHandler(
            "hebaali",
            lambda update, context: _admin_entry(
                update,
                context,
                bot_module,
                admin_id,
            ),
        ),
        group=-1,
    )
