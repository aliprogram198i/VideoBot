"""Runtime bootstrap for AliBot's canonical admin layer.

Python imports ``sitecustomize`` during interpreter startup when it is present
on ``sys.path``. We use that hook only to install the existing canonical
``admin_layer_v2`` immediately before the bot starts polling. This avoids
rewriting the large legacy ``bot.py`` registration block while allowing the
canonical layer to remove duplicate legacy admin handlers first.

The same bootstrap also provides a narrow, opt-in Instagram authentication
bridge for yt-dlp subprocesses. It is disabled unless
``INSTAGRAM_COOKIES_B64`` or ``INSTAGRAM_COOKIES_FILE`` is configured. No
cookie value is ever logged.
"""

import asyncio
import base64
import logging
import os
import tempfile

_LOG = logging.getLogger(__name__)
_COOKIE_FILE = None


def _instagram_cookie_file():
    """Return a private Netscape cookie file for yt-dlp, if configured."""
    global _COOKIE_FILE

    if _COOKIE_FILE:
        return _COOKIE_FILE

    configured_file = os.getenv("INSTAGRAM_COOKIES_FILE", "").strip()
    configured_b64 = os.getenv("INSTAGRAM_COOKIES_B64", "").strip()

    if not configured_file and not configured_b64:
        return None

    try:
        if configured_file:
            path = os.path.realpath(configured_file)
            if not os.path.isfile(path):
                _LOG.warning("Instagram cookie file is configured but missing")
                return None
            _COOKIE_FILE = path
            return _COOKIE_FILE

        raw = base64.b64decode(configured_b64, validate=True)
        if not raw:
            raise ValueError("empty cookie payload")
        text = raw.decode("utf-8")
        if not text.lstrip().startswith(("# HTTP Cookie File", "# Netscape HTTP Cookie File")):
            raise ValueError("cookie payload is not in Netscape format")

        fd, path = tempfile.mkstemp(prefix="alibot_instagram_", suffix=".cookies.txt")
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")

        _COOKIE_FILE = path
        return _COOKIE_FILE
    except Exception as exc:
        _LOG.warning("Instagram cookie bridge could not initialize: %s", type(exc).__name__)
        return None


def _is_instagram_yt_dlp_command(args):
    try:
        values = [str(item) for item in args]
    except Exception:
        return False
    return any(value.startswith(("http://instagram.com/", "https://instagram.com/", "http://www.instagram.com/", "https://www.instagram.com/")) for value in values)


def _patch_yt_dlp_subprocesses():
    """Inject optional Instagram cookies only into yt-dlp Instagram commands."""
    cookie_file = _instagram_cookie_file()
    if not cookie_file:
        return

    original_async_exec = asyncio.create_subprocess_exec
    if getattr(original_async_exec, "_alibot_instagram_cookie_bridge", False):
        return

    async def guarded_create_subprocess_exec(*args, **kwargs):
        command = list(args)
        executable = str(command[0]) if command else ""
        if (
            "yt_dlp" in executable
            or "yt-dlp" in executable
            or any(str(item) == "yt_dlp" for item in command)
        ) and _is_instagram_yt_dlp_command(command):
            if "--cookies" not in command:
                command[1:1] = ["--cookies", cookie_file]
        return await original_async_exec(*command, **kwargs)

    guarded_create_subprocess_exec._alibot_instagram_cookie_bridge = True
    asyncio.create_subprocess_exec = guarded_create_subprocess_exec
    _LOG.info("Instagram authentication bridge: ENABLED")


def _install_admin_runtime_guard():
    try:
        from telegram import CallbackQuery
        from telegram.error import BadRequest
        from telegram.ext import Application
    except Exception:
        return

    original_edit_message_text = CallbackQuery.edit_message_text
    if not getattr(original_edit_message_text, "_alibot_noop_guard", False):
        async def guarded_edit_message_text(self, *args, **kwargs):
            try:
                return await original_edit_message_text(self, *args, **kwargs)
            except BadRequest as exc:
                if str(exc).startswith("Message is not modified"):
                    return None
                raise

        guarded_edit_message_text._alibot_noop_guard = True
        CallbackQuery.edit_message_text = guarded_edit_message_text

    original = Application.run_polling
    if getattr(original, "_alibot_admin_guard", False):
        return

    def guarded_run_polling(self, *args, **kwargs):
        if not getattr(self, "_alibot_admin_layer_installed", False):
            try:
                import __main__ as bot_module
                from plugins.admin_layer_v2 import register_admin_layer

                admin_id = getattr(bot_module, "ADMIN_ID", None)
                if admin_id is None:
                    raise RuntimeError("ADMIN_ID is missing")

                register_admin_layer(self, bot_module, int(admin_id))
                self._alibot_admin_layer_installed = True
            except Exception:
                _LOG.exception("AliBot canonical admin layer installation failed")
                raise

        return original(self, *args, **kwargs)

    guarded_run_polling._alibot_admin_guard = True
    Application.run_polling = guarded_run_polling


_patch_yt_dlp_subprocesses()
_install_admin_runtime_guard()
