"""Staged runtime entrypoint for AliBot.

Keeps the legacy bot implementation intact while composing isolated runtime
layers before polling starts. Production configuration and Telegram polling
behavior are otherwise unchanged.
"""

import fcntl
import importlib
import os
import time

LOCK_PATH = "/tmp/alibot-single-instance.lock"
STARTUP_GRACE_SECONDS = 15
LOCK_TIMEOUT_SECONDS = 45
LOCK_RETRY_SECONDS = 1


def normalize_runtime_environment():
    """Normalize deployment-provided secrets before importing the bot module.

    Railway/environment editors can preserve a trailing CR/LF when a secret is
    pasted from another terminal or text source. python-telegram-bot then sees
    that character as part of the Telegram API URL and raises InvalidURL.
    Never log the secret itself.
    """
    token = os.getenv("BOT_TOKEN")
    if token is not None:
        normalized = token.strip()
        if normalized != token:
            os.environ["BOT_TOKEN"] = normalized
            print("🧹 Runtime environment: normalized BOT_TOKEN whitespace.", flush=True)


def acquire_single_instance_lock():
    """Acquire the local process lock without blocking indefinitely."""
    lock_file = open(LOCK_PATH, "w", encoding="utf-8")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    print(f"🛡️ Startup guard: waiting {STARTUP_GRACE_SECONDS}s before lock acquisition.", flush=True)
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
                raise RuntimeError("Another AliBot polling process is still running; startup lock timeout exceeded.")
            print(f"⏳ Another AliBot process is active; retrying for up to {int(remaining)}s.", flush=True)
            time.sleep(min(LOCK_RETRY_SECONDS, remaining))


def main() -> None:
    normalize_runtime_environment()
    lock_file = acquire_single_instance_lock()
    try:
        bot_module = importlib.import_module("bot")
        runtime_config = importlib.import_module("plugins.runtime_config")
        register_user_activity = importlib.import_module("plugins.user_activity").register_user_activity
        register_features = importlib.import_module("plugins.recovered_features").register_recovered_features
        register_smart_search_pro = importlib.import_module("plugins.smart_search_pro").register_smart_search_pro
        register_admin_layer = importlib.import_module("plugins.admin_layer").register_admin_layer
        register_admin_user_links = importlib.import_module("plugins.admin_user_links").register_admin_user_links
        install_yoinku_compat = importlib.import_module("plugins.yoinku_compat").install
        install_download_guards = importlib.import_module("security.download_guard").install_download_guards

        runtime_config.apply_to_bot_module(bot_module)
        install_yoinku_compat(bot_module)
        install_download_guards(bot_module)

        original_run_polling = Application.run_polling
        registered = False

        def run_polling_with_layers(self, *args, **kwargs):
            nonlocal registered
            if not registered:
                register_user_activity(self, bot_module)
                register_smart_search_pro(self, bot_module)
                register_features(self, bot_module, bot_module.ADMIN_ID)

                # Admin layer must run before the user-links overlay. The admin
                # layer removes legacy callback routes, including the historical
                # admin_user_view/user patterns. Registering user-links first
                # would therefore remove the new handler we intend to keep.
                register_admin_layer(self, bot_module, bot_module.ADMIN_ID)
                register_admin_user_links(self, bot_module.get_db, bot_module.ADMIN_ID)

                print("🛡️ Admin layer registered", flush=True)
                print("🔗 Admin user download-links layer registered", flush=True)
                print("👤 User activity middleware registered", flush=True)
                registered = True
            return original_run_polling(self, *args, **kwargs)

        Application.run_polling = run_polling_with_layers
        bot_module.main()
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
            print("🛡️ Single-instance guard released.", flush=True)


if __name__ == "__main__":
    main()
