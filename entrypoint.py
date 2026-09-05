"""Production entrypoint for AliBot.

Provides a local single-instance guard plus a short startup grace period so
Railway can terminate the previous polling process before Telegram polling
starts. Telegram-side token ownership is unchanged.
"""

import fcntl
import importlib
import os
import sys
import time

from telegram.ext import Application

LOCK_PATH = "/app/data/alibot-single-instance.lock" if os.path.isdir("/app/data") else "/tmp/alibot-single-instance.lock"
STARTUP_GRACE_SECONDS = 15


def main() -> None:
    lock_file = open(LOCK_PATH, "w", encoding="utf-8")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

    print(
        f"🛡️ Single-instance guard acquired; waiting {STARTUP_GRACE_SECONDS}s before Telegram polling.",
        flush=True,
    )
    time.sleep(STARTUP_GRACE_SECONDS)

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
