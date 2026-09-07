"""Isolated administration layer for AliBot.

This module is the bootstrap boundary for the admin UI. It does not own
downloader logic or user state; it only connects the existing admin
control-center callbacks to the running Telegram application and provides a
single safe /hebaali entry route.
"""

from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop, CommandHandler, ContextTypes, CallbackQueryHandler

from .admin_control_center import _home_text, admin_keyboard, register_admin_control_center


async def _admin_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id: int,
) -> None:
    """Open the exact same admin home view used by every Home navigation path."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message or user.id != admin_id:
        return

    await message.reply_text(
        _home_text(),
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )
    raise ApplicationHandlerStop


def _remove_legacy_hebaali_handlers(app: Any) -> int:
    """Remove only the historical /hebaali command before installing the owned route."""
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


def _remove_legacy_admin_callback_handlers(app: Any) -> int:
    """Remove only callback routes now exclusively owned by the isolated admin layer.

    This is deliberately prefix based and runs before the new admin modules are
    registered. Unrelated admin features such as the existing statistics route
    remain untouched, while duplicate Home/users/user-history routes are removed
    so Telegram cannot dispatch the same callback into two dashboards.
    """
    prefixes = (
        r"^admin_home$",
        r"^admin_users_",
        r"^user_",
        r"^admin_user_view_",
        r"^admin_user_clear_",
        r"^admin_global_history_reset$",
        r"^admin_control_center$",
        r"^admin_records$",
        r"^admin_health$",
        r"^admin_audit$",
        r"^admin_roles$",
        r"^admin_smart_operations$",
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
    """Register the admin layer with one owner for the /hebaali route."""
    removed_command = _remove_legacy_hebaali_handlers(app)
    removed_callbacks = _remove_legacy_admin_callback_handlers(app)

    if removed_command:
        print("🧩 Legacy /hebaali handler removed; admin layer owns the route", flush=True)
    if removed_callbacks:
        print(
            f"🧩 Removed {removed_callbacks} legacy admin callback handler(s); isolated admin layer owns those routes",
            flush=True,
        )

    # The control center owns registration of all admin callbacks, including
    # the global-history reset. Keeping a single registration point prevents
    # duplicate callback handlers and conflicting responses.
    register_admin_control_center(app, bot_module.get_db, admin_id)

    # Group -1 gives the owner route priority over unrelated legacy routers.
    # The legacy /hebaali handler is removed above, so this command is unique.
    app.add_handler(
        CommandHandler(
            "hebaali",
            lambda update, context: _admin_entry(update, context, admin_id),
        ),
        group=-1,
    )
