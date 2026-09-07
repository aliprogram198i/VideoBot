"""Production entrypoint for AliBot.

Provides a bounded single-instance guard plus a short startup grace period so
Railway can terminate the previous polling process before Telegram polling
starts. Telegram-side token ownership is unchanged.
"""

import fcntl
import importlib
import os
import time

from telegram.ext import Application

LOCK_PATH = "/tmp/alibot-single-instance.lock"
STARTUP_GRACE_SECONDS = 15
LOCK_TIMEOUT_SECONDS = 45
LOCK_RETRY_SECONDS = 1


def acquire_single_instance_lock():
    """Acquire the local process lock without ever blocking indefinitely."""
    lock_file = open(LOCK_PATH, "w", encoding="utf-8")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS

    print(
        f"🛡️ Startup guard: waiting {STARTUP_GRACE_SECONDS}s before lock acquisition.",
        flush=True,
    )
    time.sleep(STARTUP_GRACE_SECONDS)

    while True:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            print("🛡️ Single-instance guard acquired.", flush=True)
            return lock_file
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                lock_file.close()
                raise RuntimeError(
                    "Another AliBot polling process is still running; startup lock timeout exceeded."
                )
            print(
                f"⏳ Another AliBot process is active; retrying for up to {int(remaining)}s.",
                flush=True,
            )
            time.sleep(min(LOCK_RETRY_SECONDS, remaining))


def main() -> None:
    lock_file = acquire_single_instance_lock()
    try:
        bot_module = importlib.import_module("bot")
        register_features = importlib.import_module(
            "plugins.recovered_features"
        ).register_recovered_features
        register_smart_search_pro = importlib.import_module(
            "plugins.smart_search_pro"
        ).register_smart_search_pro
        register_admin_layer = importlib.import_module(
            "plugins.admin_layer"
        ).register_admin_layer

        original_run_polling = Application.run_polling
        registered = False

        def run_polling_with_restored_features(self, *args, **kwargs):
            nonlocal registered
            if not registered:
                # Smart Search Pro is intentionally registered first at group -2.
                # It stops ordinary text updates before the legacy catch-all search
                # handler at group -1 can process them. Admin workflows explicitly
                # bypass Pro search and continue to the existing admin router.
                register_smart_search_pro(self, bot_module)
                register_features(self, bot_module, bot_module.ADMIN_ID)
                try:
                    register_admin_layer(self, bot_module, bot_module.ADMIN_ID)
                    print("🛡️ Admin layer registered", flush=True)
                except Exception as exc:
                    # Administration is optional at runtime; a dashboard failure
                    # must never prevent the downloader bot from starting.
                    print(
                        f"⚠️ Admin layer registration failed: {type(exc).__name__}",
                        flush=True,
                    )
                registered = True
            return original_run_polling(self, *args, **kwargs)

        Application.run_polling = run_polling_with_restored_features
        bot_module.main()
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
            print("🛡️ Single-instance guard released.", flush=True)


if __name__ == "__main__":
    main()
