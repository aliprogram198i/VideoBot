"""Canonical isolated administration runtime for AliBot.

This module is the single runtime owner of the administrative UI. It deliberately
registers each admin capability once and never registers the legacy users/history
workspace, preventing duplicate callback ownership.
"""

from __future__ import annotations

import re
from typing import Any

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler

from .admin_control_center import (
    _home_text,
    admin_keyboard,
    admin_control_center_callback,
    admin_records_callback,
    admin_health_callback,
    admin_audit_callback,
    admin_roles_callback,
    init_admin_control_center,
)
from .admin_users import process_user_message, process_search, register_admin_users, user_view_callback
from .admin_users_plus import register_admin_users_plus
from .admin_user_links import register_admin_user_links
from .admin_download_log import register_admin_download_log
from .download_log_enrichment import register_download_log_enrichment
from .admin_stats import register_admin_stats
from .admin_broadcast import register_admin_broadcast, process_broadcast
from .admin_storage import register_admin_storage
from .admin_global_history import register_admin_global_history
from .admin_ai import register_admin_ai
from .smart_operations import register_smart_operations
from .admin_user_history import clear_prompt_callback, clear_confirm_callback


async def _admin_entry(update, context, admin_id: int) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or user.id != admin_id:
        return
    await message.reply_text(_home_text(), parse_mode="HTML", reply_markup=admin_keyboard())
    raise ApplicationHandlerStop


def _remove_legacy_admin_handlers(app: Any) -> tuple[int, int]:
    """Remove retired dashboard handlers before the canonical layer is installed."""
    retired_commands = {"hebaali", "stats", "broadcast"}
    retired_prefixes = (
        r"^admin_home$", r"^admin_users_", r"^user_\d+$", r"^admin_user_view_",
        r"^admin_user_clear_", r"^admin_global_history_reset$", r"^admin_control_center$",
        r"^admin_records$", r"^admin_health$", r"^admin_audit$", r"^admin_roles$",
        r"^admin_smart_operations$", r"^admin_ai$", r"^ai_test$", r"^ai_stats$",
        r"^ai_report$", r"^ai_users$", r"^ai_websites$", r"^ai_errors$", r"^admin_ai_refresh$",
        r"^ai_retry$", r"^admin_dashboard_", r"^admin_stats$", r"^admin_recent_downloads$",
        r"^admin_top_users$", r"^admin_top_websites$", r"^admin_broadcast$",
        r"^admin_delete_broadcasts$", r"^admin_storage$", r"^admin_storage_confirm$",
        r"^admin_storage_cancel$", r"^ban_", r"^unban_", r"^delete_user_",
        r"^confirm_delete_", r"^message_user_", r"^admin_search$",
    )
    commands_removed = 0
    callbacks_removed = 0
    handlers_by_group = getattr(app, "handlers", {})
    for group, handlers in list(handlers_by_group.items()):
        kept = []
        for handler in handlers:
            commands = getattr(handler, "commands", None)
            if isinstance(handler, CommandHandler) and commands and any(c in retired_commands for c in commands):
                commands_removed += 1
                continue
            pattern = getattr(handler, "pattern", None)
            pattern_text = getattr(pattern, "pattern", None) or (pattern if isinstance(pattern, str) else "")
            if any(pattern_text == prefix or pattern_text.startswith(prefix[:-1]) for prefix in retired_prefixes):
                callbacks_removed += 1
                continue
            kept.append(handler)
        handlers_by_group[group] = kept
    return commands_removed, callbacks_removed


def _register_core_callback(app, callback, pattern: str, get_db, owner_id: int, group: int = -100) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: callback(u, c, get_db, owner_id), pattern=pattern), group=group)


async def _clear_cancel(update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    if not (update.effective_user and update.effective_user.id == owner_id):
        await query.answer()
        return
    match = re.fullmatch(r"admin_user_clear_cancel_(\d+)", query.data or "")
    if not match:
        await query.answer()
        return
    await query.answer("تم الإلغاء")
    await user_view_callback(update, context, get_db, owner_id, callback_data=f"admin_user_view_{int(match.group(1))}")


def register_admin_layer(app: Any, bot_module: Any, admin_id: int) -> None:
    """Install the one canonical admin layer and its non-overlapping submodules."""
    removed_commands, removed_callbacks = _remove_legacy_admin_handlers(app)
    print(f"🧩 Retired admin handlers removed: commands={removed_commands}, callbacks={removed_callbacks}", flush=True)

    get_db = bot_module.get_db
    init_admin_control_center(get_db, admin_id)
    register_download_log_enrichment(bot_module)

    # Canonical dashboard/navigation: one owner per top-level callback.
    _register_core_callback(app, admin_control_center_callback, r"^admin_home$", get_db, admin_id)
    _register_core_callback(app, admin_records_callback, r"^admin_records$", get_db, admin_id)
    _register_core_callback(app, admin_health_callback, r"^admin_health$", get_db, admin_id)
    _register_core_callback(app, admin_audit_callback, r"^admin_audit$", get_db, admin_id)
    _register_core_callback(app, admin_roles_callback, r"^admin_roles$", get_db, admin_id)

    # Canonical user workspace: list/filters + user detail/actions + download links.
    register_admin_users_plus(app, get_db, admin_id)
    register_admin_users(app, get_db, admin_id)
    register_admin_user_links(app, get_db, admin_id)

    # History contributes only its destructive confirmation workflow.
    app.add_handler(CallbackQueryHandler(lambda u, c: clear_prompt_callback(u, c, get_db, admin_id), pattern=r"^admin_user_clear_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: clear_confirm_callback(u, c, get_db, admin_id), pattern=r"^admin_user_clear_confirm_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: _clear_cancel(u, c, get_db, admin_id), pattern=r"^admin_user_clear_cancel_\d+$"), group=-1)

    # Remaining administrative capabilities are each registered exactly once.
    register_admin_stats(app, get_db, admin_id)
    register_admin_broadcast(app, get_db, admin_id)
    register_admin_storage(app, admin_id)
    register_admin_global_history(app, get_db, admin_id)
    register_admin_ai(app, get_db, admin_id)
    register_smart_operations(app, get_db, admin_id)
    register_admin_download_log(app, get_db, admin_id)

    bot_module.process_broadcast = lambda update, context: process_broadcast(update, context, get_db, admin_id)
    bot_module.process_user_message = lambda update, context: process_user_message(update, context, admin_id)
    bot_module.process_admin_search = lambda update, context: process_search(update, context, get_db, admin_id)

    app.add_handler(CommandHandler("hebaali", lambda update, context: _admin_entry(update, context, admin_id)), group=-200)
    print("🛡️ Canonical isolated admin layer registered", flush=True)
