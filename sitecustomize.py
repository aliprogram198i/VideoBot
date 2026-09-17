"""Runtime bootstrap for AliBot's canonical runtime guards.

The existing admin bootstrap remains unchanged in behavior. Before polling,
Telegram message download callbacks are wrapped by a strict resolver that
verifies the exact channel/message identity. Generic recovery stages are then
blocked for that verified Telegram request, so an unrelated video can never be
accepted as a successful fallback.
"""

import functools
import logging

_LOG = logging.getLogger(__name__)


def _telegram_failure_text(language: str) -> str:
    if language == "en":
        return (
            "❌ Telegram post verification failed.\n\n"
            "The requested post could not be verified, so no other source or fallback was used."
        )
    if language == "tr":
        return (
            "❌ Telegram gönderisi doğrulanamadı.\n\n"
            "İstenen gönderi doğrulanamadığı için başka bir kaynak veya yedek yöntem kullanılmadı."
        )
    if language == "de":
        return (
            "❌ Der Telegram-Beitrag konnte nicht verifiziert werden.\n\n"
            "Da der angeforderte Beitrag nicht verifiziert werden konnte, wurde kein anderer Fallback verwendet."
        )
    return (
        "❌ تعذر التحقق من منشور Telegram.\n\n"
        "لذلك لم يتم استخدام أي مصدر أو مسار بديل حتى لا يتم إرسال فيديو مختلف عن المنشور المطلوب."
    )


def _install_telegram_message_guard(bot_module, application):
    """Install an exact-message guard around the existing download handler."""
    if getattr(application, "_alibot_telegram_guard_installed", False):
        return

    from telegram_layer.telegram_message_resolver import (
        enter_telegram_strict_mode,
        exit_telegram_strict_mode,
        parse_telegram_message_url,
        resolve_telegram_message,
        telegram_strict_mode,
    )

    original_download = getattr(bot_module, "download_media", None)
    if original_download is None:
        raise RuntimeError("download_media is missing")

    @functools.wraps(original_download)
    async def guarded_download_media(update, context):
        url = context.user_data.get("video_url") if context else None
        ref = parse_telegram_message_url(url or "")

        if ref is None:
            return await original_download(update, context)

        query = getattr(update, "callback_query", None)
        user = getattr(update, "effective_user", None)

        try:
            canonical_url, info = await resolve_telegram_message(url)
            if not isinstance(info, dict):
                raise RuntimeError("Telegram resolver returned invalid metadata")
        except Exception as exc:
            _LOG.warning(
                "Telegram message verification blocked download for %s/%s: %s",
                ref.channel,
                ref.message_id,
                type(exc).__name__,
            )
            if query is not None:
                try:
                    await query.answer()
                except Exception:
                    pass
                try:
                    language = (
                        bot_module.get_language(user.id)
                        if user is not None and hasattr(bot_module, "get_language")
                        else "ar"
                    ) or "ar"
                    await query.edit_message_text(_telegram_failure_text(language))
                except Exception:
                    pass
            return

        context.user_data["video_url"] = canonical_url
        token = enter_telegram_strict_mode()
        try:
            return await original_download(update, context)
        finally:
            exit_telegram_strict_mode(token)

    guarded_download_media._alibot_telegram_guard = True

    replaced_handler = False
    for handlers in getattr(application, "handlers", {}).values():
        for handler in handlers:
            if getattr(handler, "callback", None) is original_download:
                handler.callback = guarded_download_media
                replaced_handler = True

    if not replaced_handler:
        raise RuntimeError("Telegram download handler was not found")

    fallback_specs = (
        ("download_with_smart_extraction", (None, {"status": "telegram_strict_blocked"})),
        ("download_with_yoinku", (None, {"status": "telegram_strict_blocked"})),
        ("download_with_fallback", (None, None, None, {"status": "telegram_strict_blocked"})),
    )

    for function_name, blocked_result in fallback_specs:
        original = getattr(bot_module, function_name, None)
        if original is None or getattr(original, "_alibot_telegram_strict_guard", False):
            continue

        @functools.wraps(original)
        async def guarded_fallback(*args, __original=original, __blocked=blocked_result, **kwargs):
            if telegram_strict_mode():
                return __blocked
            return await __original(*args, **kwargs)

        guarded_fallback._alibot_telegram_strict_guard = True
        setattr(bot_module, function_name, guarded_fallback)

    application._alibot_telegram_guard_installed = True
    _LOG.info("🛡️ Telegram exact-message resolver: ENABLED")


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

        if not getattr(self, "_alibot_telegram_guard_installed", False):
            try:
                import __main__ as bot_module
                _install_telegram_message_guard(bot_module, self)
            except Exception:
                _LOG.exception("AliBot Telegram exact-message guard installation failed")
                raise

        return original(self, *args, **kwargs)

    guarded_run_polling._alibot_admin_guard = True
    Application.run_polling = guarded_run_polling


_install_admin_runtime_guard()
