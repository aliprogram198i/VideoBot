"""Production entrypoint for AliBot.

Provides a local single-instance guard plus a short startup grace period so
Railway can terminate the previous polling process before Telegram polling
starts. Telegram-side token ownership is unchanged.
"""

import fcntl
import importlib
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

LOCK_PATH = "/app/data/alibot-single-instance.lock" if os.path.isdir("/app/data") else "/tmp/alibot-single-instance.lock"
STARTUP_GRACE_SECONDS = 15


def _install_admin_panel_buttons(bot_module):
    """Make restored admin actions visible in the actual production panel."""
    original = bot_module.admin_keyboard

    def admin_keyboard_with_group_broadcast():
        markup = original()
        rows = [list(row) for row in markup.inline_keyboard]
        if not any(
            button.callback_data == "group_broadcast_panel"
            for row in rows
            for button in row
        ):
            rows.append([
                InlineKeyboardButton(
                    "📢 إعلان المجموعات",
                    callback_data="group_broadcast_panel",
                )
            ])
        return InlineKeyboardMarkup(rows)

    bot_module.admin_keyboard = admin_keyboard_with_group_broadcast


def main() -> None:
    lock_file = open(LOCK_PATH, "w", encoding="utf-8")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

    print(
        f"🛡️ Single-instance guard acquired; waiting {STARTUP_GRACE_SECONDS}s before Telegram polling.",
        flush=True,
    )
    time.sleep(STARTUP_GRACE_SECONDS)

    bot_module = importlib.import_module("bot")
    register_features = importlib.import_module("plugins.recovered_features").register_recovered_features
    register_broadcast = importlib.import_module("plugins.broadcast_media").register_broadcast_media
    register_group_broadcast = importlib.import_module("plugins.group_broadcast").register_group_broadcast

    original_run_polling = Application.run_polling
    registered = False

    def run_polling_with_restored_features(self, *args, **kwargs):
        nonlocal registered
        if not registered:
            register_features(self, bot_module, bot_module.ADMIN_ID)
            _install_admin_panel_buttons(bot_module)
            register_broadcast(self, bot_module, bot_module.ADMIN_ID)
            register_group_broadcast(self, bot_module, bot_module.ADMIN_ID)
            registered = True
        return original_run_polling(self, *args, **kwargs)

    Application.run_polling = run_polling_with_restored_features
    bot_module.main()


if __name__ == "__main__":
    main()
