from pathlib import Path
"""Staged runtime entrypoint for AliBot.

Keeps the bot runtime stable while composing isolated runtime layers before polling starts.
The administrative UI is owned exclusively by plugins.admin_layer_v2.
"""

import fcntl
import importlib
import os
import time

from storage_lifecycle import cleanup_on_startup

from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

LOCK_PATH = str(Path(__file__).resolve().parent / ".alibot-single-instance.lock")
STARTUP_GRACE_SECONDS = 15
LOCK_TIMEOUT_SECONDS = 45
LOCK_RETRY_SECONDS = 1


def normalize_runtime_environment():
    """Normalize deployment-provided secrets before importing the bot module."""
    token = os.getenv("BOT_TOKEN")
    if token is not None:
        normalized = token.replace("\r", "").replace("\n", "").replace("\t", "").strip()
        if normalized != token:
            os.environ["BOT_TOKEN"] = normalized
            print("🧹 Runtime environment: normalized BOT_TOKEN control characters.", flush=True)


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
        cleanup = cleanup_on_startup()
        print(
            "🧹 Transient storage cleanup: removed_files=%d removed_dirs=%d skipped=%d"
            % (cleanup["removed_files"], cleanup["removed_dirs"], cleanup["skipped"]),
            flush=True,
        )
        bot_module = importlib.import_module("bot")
        runtime_config = importlib.import_module("plugins.runtime_config")
        register_user_activity = importlib.import_module("plugins.user_activity").register_user_activity
        register_features = importlib.import_module("plugins.recovered_features").register_recovered_features
        register_smart_search_pro = importlib.import_module("plugins.smart_search_pro").register_smart_search_pro
        register_user_features = importlib.import_module("plugins.user_features").register_user_features
        register_user_location = importlib.import_module("plugins.user_location").register_user_location
        register_enhancements = importlib.import_module("plugins.alibot_enhancements").register
        register_rich_broadcast_confirm = importlib.import_module("plugins.alibot_enhancements")._broadcast_confirm
        register_rich_broadcast_capture = importlib.import_module("plugins.alibot_enhancements")._broadcast_capture
        register_library_search_message = importlib.import_module("plugins.alibot_enhancements")._library_search_message
        register_smart_search_handler = importlib.import_module("plugins.smart_search_pro")._search_handler
        install_shhaiid4u_resolver = importlib.import_module("downloader.shhaiid4u_resolver").install
        install_shhaiid4u_network_discovery = importlib.import_module("downloader.shhaiid4u_network_discovery").install
        install_shhaiid4u_player_bridge = importlib.import_module("downloader.shhaiid4u_player_bridge").install
        install_smart_media_bridge = importlib.import_module("downloader.smart_media_bridge").install
        install_adaptive_orchestrator = importlib.import_module("downloader.adaptive_download_orchestrator").install
        install_movie_source_guard = importlib.import_module("downloader.movie_source_guard").install
        install_telegram_media_retry = importlib.import_module("telegram_layer.media_retry").install_telegram_media_retry
        install_download_retry = importlib.import_module("plugins.download_retry").install_download_retry
        register_download_retry = importlib.import_module("plugins.download_retry").register_download_retry
        register_admin_layer = importlib.import_module("plugins.admin_layer_v2").register_admin_layer
        register_whatsapp_audio = importlib.import_module("plugins.whatsapp_audio").register_whatsapp_audio
        install_yoinku_compat = importlib.import_module("plugins.yoinku_compat").install
        install_download_guards = importlib.import_module("security.download_guard").install_download_guards
        install_multi_url_batch = importlib.import_module("plugins.multi_url_batch").install

        runtime_config.apply_to_bot_module(bot_module)
        install_yoinku_compat(bot_module)
        install_download_guards(bot_module)
        # Keep all Shhaiid4u layers isolated. Install the older layers first;
        # the player bridge becomes the outer wrapper and gets first opportunity.
        install_shhaiid4u_resolver(bot_module)
        install_shhaiid4u_network_discovery(bot_module)
        install_shhaiid4u_player_bridge(bot_module)
        # Install the movie gate before Smart Media Bridge so rejected direct
        # artifacts are treated as a failed attempt and the bridge can continue
        # through its existing candidate/browser fallback chain.
        install_movie_source_guard(bot_module)
        install_smart_media_bridge(bot_module)
        # The adaptive layer is deliberately outermost around candidate
        # extraction. It only reorders already-discovered candidates and cannot
        # bypass validation, download guards, or provider-specific controls.
        install_adaptive_orchestrator(bot_module)
        install_multi_url_batch(bot_module)
        install_telegram_media_retry()
        install_download_retry(bot_module)

        original_run_polling = Application.run_polling
        registered = False

        async def admin_text_router(update, context):
            """Route admin text by active workflow before generic text search.

            The rich broadcast and library handlers share group -4. Their
            broad text filters mean the first matching handler can consume the
            update without stopping propagation. This router makes the active
            admin workflow explicit while preserving the existing smart-search
            behavior for ordinary admin text.
            """
            if not update.message or not update.effective_user:
                return
            if update.effective_user.id != bot_module.ADMIN_ID:
                return
            if context.user_data.get("rich_broadcast_waiting"):
                await register_rich_broadcast_capture(update, context)
                return
            if context.user_data.get("library_searching"):
                await register_library_search_message(update, context)
                return
            await register_smart_search_handler(update, context, bot_module)

        def run_polling_with_layers(self, *args, **kwargs):
            nonlocal registered
            if not registered:
                register_user_activity(self, bot_module)
                register_smart_search_pro(self, bot_module)
                register_user_features(self, bot_module)
                register_user_location(self, bot_module)
                register_features(self, bot_module, bot_module.ADMIN_ID)
                register_whatsapp_audio(self, bot_module)
                register_download_retry(self, bot_module)

                # Install the canonical admin layer first so its legacy-handler
                # cleanup cannot remove the rich broadcast entrypoint below.
                register_admin_layer(self, bot_module, bot_module.ADMIN_ID)
                register_enhancements(self, bot_module)

                # Admin text must be routed before the broad library/search text
                # handlers when a stateful admin workflow is active.
                self.add_handler(
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND & filters.User(user_id=bot_module.ADMIN_ID),
                        admin_text_router,
                    ),
                    group=-5,
                )

                # The rich confirmation must run before any generic admin
                # callback handler. This preserves the preview -> send flow even
                # when another admin module owns a broader callback pattern.
                self.add_handler(
                    CallbackQueryHandler(
                        register_rich_broadcast_confirm,
                        pattern=r"^admin_rich_broadcast_confirm$",
                    ),
                    group=-1000,
                )

                print("🛡️ Canonical isolated admin layer active", flush=True)
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
