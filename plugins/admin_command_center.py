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
    """Compatibility facade over the canonical top-level admin navigation."""
    return admin_keyboard()


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



def register_admin_command_center(app, get_db, owner_id: int) -> None:
    app.add_handler(
        __import__("telegram.ext", fromlist=["CallbackQueryHandler"]).CallbackQueryHandler(
            lambda u, c: command_center_callback(u, c, get_db, owner_id),
            pattern=r"^admin_home$|^admin_control_center$",
        ),
        group=-300,
    )
