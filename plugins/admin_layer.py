"""Isolated administration layer for AliBot.

This module is the bootstrap boundary for the admin UI. It does not own
downloader logic or user state; it only connects the existing admin
control-center callbacks to the running Telegram application and provides a
single safe /hebaali entry route.
"""

from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop, CommandHandler, ContextTypes

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


def register_admin_layer(app: Any, bot_module: Any, admin_id: int) -> None:
    """Register the admin layer with one owner for the /hebaali route."""
    removed = _remove_legacy_hebaali_handlers(app)
    if removed:
        print("🧩 Legacy /hebaali handler removed; admin layer owns the route", flush=True)

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
