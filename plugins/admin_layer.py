"""Isolated administration layer for AliBot.

This module is the runtime boundary for the administrative UI. Legacy admin
handlers may remain in bot.py temporarily for source-level migration safety,
but their retired routes are removed before the isolated modules are added.
"""

from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop, CommandHandler, ContextTypes, CallbackQueryHandler

from .admin_control_center import _home_text, admin_keyboard, register_admin_control_center
from .admin_broadcast import process_broadcast
from .admin_users import process_user_message, process_search
from .admin_users_plus import register_admin_users_plus
from .admin_download_log import register_admin_download_log
from .download_log_enrichment import register_download_log_enrichment


async def _admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id: int) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or user.id != admin_id:
        return
    await message.reply_text(_home_text(), parse_mode="HTML", reply_markup=admin_keyboard())
    raise ApplicationHandlerStop


def _remove_legacy_admin_command_handlers(app: Any) -> int:
    """Remove commands now owned by isolated admin modules."""
    retired = {"hebaali", "stats", "broadcast"}
    removed = 0
    handlers_by_group = getattr(app, "handlers", {})
    for group, handlers in list(handlers_by_group.items()):
        kept = []
        for handler in handlers:
            commands = getattr(handler, "commands", None)
            if isinstance(handler, CommandHandler) and commands and any(c in retired for c in commands):
                removed += 1
                continue
            kept.append(handler)
        handlers_by_group[group] = kept
    return removed


def _remove_legacy_admin_callback_handlers(app: Any) -> int:
    """Remove every callback family whose runtime owner has moved to plugins."""
    prefixes = (
        r"^admin_home$", r"^admin_users_", r"^user_\d+$", r"^admin_user_view_", r"^admin_user_clear_",
        r"^admin_global_history_reset$", r"^admin_control_center$", r"^admin_records$", r"^admin_health$",
        r"^admin_audit$", r"^admin_roles$", r"^admin_smart_operations$", r"^admin_ai$", r"^ai_test$",
        r"^ai_stats$", r"^ai_report$", r"^ai_users$", r"^ai_websites$", r"^ai_errors$", r"^admin_ai_refresh$",
        r"^ai_retry$", r"^admin_dashboard_", r"^admin_stats$", r"^admin_recent_downloads$", r"^admin_top_users$",
        r"^admin_top_websites$", r"^admin_broadcast$", r"^admin_delete_broadcasts$", r"^admin_storage$",
        r"^admin_storage_confirm$", r"^admin_storage_cancel$", r"^ban_", r"^unban_", r"^delete_user_",
        r"^confirm_delete_", r"^message_user_", r"^admin_search$",
    )
    removed = 0
    handlers_by_group = getattr(app, "handlers", {})
    for group, handlers in list(handlers_by_group.items()):
        kept = []
        for handler in handlers:
            if not isinstance(handler, CallbackQueryHandler):
                kept.append(handler)
                continue
            pattern = getattr(handler, "pattern", None)
            pattern_text = getattr(pattern, "pattern", None) or (pattern if isinstance(pattern, str) else "")
            if any(pattern_text == prefix or pattern_text.startswith(prefix[:-1]) for prefix in prefixes):
                removed += 1
                continue
            kept.append(handler)
        handlers_by_group[group] = kept
    return removed


def register_admin_layer(app: Any, bot_module: Any, admin_id: int) -> None:
    removed_command = _remove_legacy_admin_command_handlers(app)
    removed_callbacks = _remove_legacy_admin_callback_handlers(app)

    if removed_command:
        print(f"🧩 Removed {removed_command} legacy admin command handler(s)", flush=True)
    if removed_callbacks:
        print(f"🧩 Removed {removed_callbacks} legacy admin callback handler(s)", flush=True)

    register_download_log_enrichment(bot_module)
    register_admin_users_plus(app, bot_module.get_db, admin_id)
    register_admin_control_center(app, bot_module.get_db, admin_id)
    register_admin_download_log(app, bot_module.get_db, admin_id)

    bot_module.process_broadcast = lambda update, context: process_broadcast(update, context, bot_module.get_db, admin_id)
    bot_module.process_user_message = lambda update, context: process_user_message(update, context, admin_id)
    bot_module.process_admin_search = lambda update, context: process_search(update, context, bot_module.get_db, admin_id)

    app.add_handler(CommandHandler("hebaali", lambda update, context: _admin_entry(update, context, admin_id)), group=-1)
