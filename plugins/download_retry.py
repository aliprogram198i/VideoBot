"""User-facing retry layer for failed downloads.

This layer wraps the existing ``bot.download_media`` entry point without
changing downloader/resolver behavior or the database schema. It remembers
only the current user's last failed URL + exact download choice and exposes a
bounded retry button when the existing download handler reports a final
failure.
"""

from __future__ import annotations

import functools
import re
from types import SimpleNamespace
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

_RETRY_CALLBACK = re.compile(r"^download_retry$")
_QUALITY_CALLBACK = re.compile(
    r"^(?:video_(?:best|1080|720|480|360)|audio_(?:best|320|256|192|128))$"
)
_MAX_MANUAL_RETRIES = 3
_STATE_KEY = "alibot_download_retry"
_RETRY_INVOCATION_KEY = "alibot_download_retry_invocation"


def _retry_label(language: str) -> str:
    return {
        "ar": "🔄 إعادة المحاولة",
        "en": "🔄 Retry download",
        "tr": "🔄 Tekrar indir",
        "de": "🔄 Erneut versuchen",
    }.get(language, "🔄 إعادة المحاولة")


def _failure_texts(bot_module: Any, language: str) -> set[str]:
    texts = getattr(bot_module, "TEXTS", {})
    localized = texts.get(language, {}) if isinstance(texts, dict) else {}
    return {
        str(localized.get("download_error", "")),
        str(localized.get("file_error", "")),
        str(localized.get("general_error", "")),
    } - {""}


def _retry_markup(language: str) -> InlineKeyboardMarkup:
    label = _retry_label(language)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=_RETRY_CALLBACK.pattern.removeprefix("^").removesuffix("$"))]
    ])


class _RetryQueryProxy:
    """Proxy a Telegram callback query and attach retry UI to final failures."""

    def __init__(self, query, context: ContextTypes.DEFAULT_TYPE, bot_module: Any):
        self._query = query
        self._context = context
        self._bot_module = bot_module
        self.data = getattr(query, "data", None)
        self.from_user = getattr(query, "from_user", None)
        self.message = getattr(query, "message", None)

    async def answer(self, *args, **kwargs):
        return await self._query.answer(*args, **kwargs)

    async def edit_message_text(self, *args, **kwargs):
        text = args[0] if args else kwargs.get("text")
        user = getattr(self._query, "from_user", None)
        language = "ar"
        if user is not None:
            try:
                language = self._bot_module.get_language(user.id) or "ar"
            except Exception:
                language = "ar"

        if isinstance(text, str) and text in _failure_texts(self._bot_module, language):
            state = self._context.user_data.get(_STATE_KEY) or {}
            if state.get("url") and state.get("choice") in _QUALITY_CALLBACK.pattern:
                retries = int(state.get("retries", 0))
                if retries < _MAX_MANUAL_RETRIES:
                    kwargs["reply_markup"] = _retry_markup(language)
                    state["retries"] = retries
                    self._context.user_data[_STATE_KEY] = state

        return await self._query.edit_message_text(*args, **kwargs)

    async def delete_message(self, *args, **kwargs):
        self._context.user_data.pop(_STATE_KEY, None)
        return await self._query.delete_message(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._query, name)


async def _wrapped_download_media(bot_module: Any, update, context):
    query = getattr(update, "callback_query", None)
    user = getattr(update, "effective_user", None)
    choice = getattr(query, "data", None)
    url = context.user_data.get("video_url")

    is_retry = bool(context.user_data.pop(_RETRY_INVOCATION_KEY, False))

    if user and url and _QUALITY_CALLBACK.fullmatch(str(choice or "")):
        if not is_retry:
            context.user_data[_STATE_KEY] = {
                "url": url,
                "choice": choice,
                "retries": 0,
            }
        else:
            state = context.user_data.get(_STATE_KEY) or {}
            state.update({"url": url, "choice": choice})
            context.user_data[_STATE_KEY] = state

    if query is not None and user is not None:
        proxy = _RetryQueryProxy(query, context, bot_module)
        wrapped_update = SimpleNamespace(
            callback_query=proxy,
            effective_user=user,
            effective_chat=getattr(update, "effective_chat", None),
            message=getattr(update, "message", None),
        )
    else:
        wrapped_update = update

    return await bot_module._alibot_original_download_media(
        wrapped_update,
        context,
    )


async def _retry_callback(update, context, bot_module):
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    state = context.user_data.get(_STATE_KEY)
    if not isinstance(state, dict) or not state.get("url") or not state.get("choice"):
        await query.answer()
        language = bot_module.get_language(user.id) or "ar"
        await query.edit_message_text(bot_module.TEXTS[language]["expired"])
        return

    retries = int(state.get("retries", 0))
    if retries >= _MAX_MANUAL_RETRIES:
        await query.answer()
        language = bot_module.get_language(user.id) or "ar"
        await query.edit_message_text(
            bot_module.TEXTS[language]["download_error"]
        )
        return

    try:
        bot_module.validate_public_http_url(state["url"])
    except Exception:
        await query.answer()
        language = bot_module.get_language(user.id) or "ar"
        await query.edit_message_text(bot_module.TEXTS[language]["expired"])
        return

    context.user_data["video_url"] = state["url"]
    state["retries"] = retries + 1
    context.user_data[_STATE_KEY] = state
    context.user_data[_RETRY_INVOCATION_KEY] = True

    await bot_module.download_media(update, context)


def install_download_retry(bot_module: Any) -> None:
    """Install the retry wrapper and register its unique callback exactly once."""
    if getattr(bot_module, "_alibot_download_retry_installed", False):
        return

    original = bot_module.download_media

    @functools.wraps(original)
    async def wrapped_download_media(update, context):
        return await _wrapped_download_media(bot_module, update, context)

    bot_module._alibot_original_download_media = original
    bot_module.download_media = wrapped_download_media
    bot_module._alibot_download_retry_installed = True

    original_run_polling = Application.run_polling
    if not getattr(original_run_polling, "_alibot_download_retry_registration", False):
        @functools.wraps(original_run_polling)
        def run_polling_with_retry(self, *args, **kwargs):
            if not getattr(self, "_alibot_download_retry_handler_registered", False):
                self.add_handler(
                    CallbackQueryHandler(
                        lambda u, c: _retry_callback(u, c, bot_module),
                        pattern=r"^download_retry$",
                    ),
                    group=-3,
                )
                self._alibot_download_retry_handler_registered = True
            return original_run_polling(self, *args, **kwargs)

        run_polling_with_retry._alibot_download_retry_registration = True
        Application.run_polling = run_polling_with_retry

    print("🔄 Download retry UX: ENABLED (bounded post-failure retry)", flush=True)
