"""Isolated administration layer for AliBot.

This module is the single bootstrap boundary for the admin UI. It does not
own downloader logic or user state; it only connects the existing admin
control-center callbacks to the running Telegram application and provides a
safe /hebaali entry route.
"""

from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop, CommandHandler, ContextTypes

from .admin_control_center import register_admin_control_center
from .smart_operations import register_smart_operations


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
        reply_markup=bot_module.admin_control_center_keyboard(),
    )
    raise ApplicationHandlerStop


def register_admin_layer(app: Any, bot_module: Any, admin_id: int) -> None:
    """Register the admin layer exactly once at the application boundary."""
    register_admin_control_center(
        app,
        bot_module.get_db,
        admin_id,
    )
    register_smart_operations(
        app,
        bot_module.get_db,
        admin_id,
    )

    # Keep /hebaali isolated from the main command router. Group -1 ensures
    # the admin route wins over any catch-all command handler already present.
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
