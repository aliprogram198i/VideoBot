"""Isolated administration layer for AliBot.

This module is the bootstrap boundary for the admin UI. It does not own
downloader logic or user state; it only connects the existing admin
control-center callbacks to the running Telegram application and provides a
single safe /hebaali entry route.
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CommandHandler, ContextTypes

from .admin_control_center import register_admin_control_center


def _admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
        [InlineKeyboardButton("🩺 صحة النظام", callback_data="admin_health")],
        [InlineKeyboardButton("🧾 سجل التدقيق", callback_data="admin_audit")],
        [InlineKeyboardButton("🛡️ الأدوار والصلاحيات", callback_data="admin_roles")],
        [InlineKeyboardButton("📊 لوحة الإحصائيات", callback_data="admin_dashboard_30")],
        [InlineKeyboardButton("🤖 Smart Operations", callback_data="admin_smart_operations")],
    ])


async def _admin_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
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


def _remove_legacy_hebaali_handlers(app: Any) -> int:
    """Remove the legacy /hebaali route before installing the owned route.

    bot.py still contains the historical command registration because it also
    owns the legacy admin surface. The bootstrap layer is the sole owner of
    the active /hebaali entry point, so remove only that exact command handler
    at runtime. No other command or callback handler is touched.
    """
    removed = 0
    handlers_by_group = getattr(app, "handlers", {})
    for group, handlers in list(handlers_by_group.items()):
        kept = []
        for handler in handlers:
            commands = getattr(handler, "commands", None)
            if isinstance(handler, CommandHandler) and commands and "hebaali" in commands:
                removed += 1
                continue
            kept.append(handler)
        if removed:
            handlers_by_group[group] = kept
    return removed


def register_admin_layer(app: Any, bot_module: Any, admin_id: int) -> None:
    """Register admin components with one active owner per route."""
    removed = _remove_legacy_hebaali_handlers(app)
    if removed:
        print("🧩 Legacy /hebaali handler removed; admin layer owns the route", flush=True)

    register_admin_control_center(app, bot_module.get_db, admin_id)

    # Group -1 gives the owner route priority over unrelated legacy routers.
    # The legacy /hebaali handler is removed above, so this route is unique.
    app.add_handler(
        CommandHandler(
            "hebaali",
            lambda update, context: _admin_entry(
                update,
                context,
                admin_id,
            ),
        ),
        group=-1,
    )
