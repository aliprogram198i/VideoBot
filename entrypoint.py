"""Production entrypoint for AliBot.

Provides the local single-instance lock and a small, explicit bootstrap hook
for optional restored features. Telegram-side token ownership is unchanged.
"""

import fcntl
import importlib
import sys

from telegram.ext import Application

LOCK_PATH = "/tmp/alibot-single-instance.lock"


def main() -> None:
    lock_file = open(LOCK_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            "❌ AliBot startup blocked: another local instance is already running.",
            flush=True,
        )
        sys.exit(75)

    bot_module = importlib.import_module("bot")
    register_features = importlib.import_module(
        "plugins.recovered_features"
    ).register_recovered_features

    original_run_polling = Application.run_polling
    registered = False

    def run_polling_with_restored_features(self, *args, **kwargs):
        nonlocal registered
        if not registered:
            register_features(self, bot_module, bot_module.ADMIN_ID)
            registered = True
        return original_run_polling(self, *args, **kwargs)

    Application.run_polling = run_polling_with_restored_features
    bot_module.main()


if __name__ == "__main__":
    main()
