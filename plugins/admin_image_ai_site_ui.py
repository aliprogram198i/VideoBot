"""Small UI bridge that adds Website Learning to the existing Image AI keyboard.

The bridge avoids modifying the established Image AI handlers, preserving their callback
ownership and behavior while exposing the new isolated site-learning entry point.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import admin_image_ai


_PATCHED = False


def patch_image_ai_keyboard() -> None:
    global _PATCHED
    if _PATCHED:
        return
    original = admin_image_ai._keyboard

    def keyboard_with_site_learning() -> InlineKeyboardMarkup:
        markup = original()
        rows = [list(row) for row in markup.inline_keyboard]
        rows.insert(2, [InlineKeyboardButton("🌐 تحليل موقع وتعليم النظام", callback_data="admin_image_ai_site")])
        return InlineKeyboardMarkup(rows)

    admin_image_ai._keyboard = keyboard_with_site_learning
    _PATCHED = True
