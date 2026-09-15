"""Fail-open multi-URL batch adapter for AliBot."""
from __future__ import annotations

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_MAX_URLS = 5
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING = ".,!?;:)]}"


def extract_urls(text: str) -> list[str]:
    seen = set()
    result = []
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(_TRAILING)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


class _MessageProxy:
    """Preserve the original message object while replacing only its text."""

    def __init__(self, message, text: str):
        self._message = message
        self.text = text

    def __getattr__(self, name):
        return getattr(self._message, name)


class _UpdateProxy:
    """Proxy an Update while replacing only the message or callback query."""

    def __init__(self, update, *, message=None, query=None):
        self._update = update
        if message is not None:
            self.message = message
        if query is not None:
            self.callback_query = query

    def __getattr__(self, name):
        return getattr(self._update, name)


class _QueryProxy:
    """Route callback UI operations to a per-URL progress message.

    The original callback query is answered only once by the first real
    download call. Later calls still receive the same callback data/user,
    while their edit/delete operations target their own Telegram message.
    This prevents the original handler from trying to edit/delete the same
    callback message multiple times.
    """

    def __init__(self, query, status_message=None):
        self._query = query
        self._status_message = status_message
        self.data = query.data
        self.from_user = query.from_user
        self.message = status_message or query.message

    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_text(self, *args, **kwargs):
        if self._status_message is None:
            return None
        return await self._status_message.edit_text(*args, **kwargs)

    async def delete_message(self, *args, **kwargs):
        if self._status_message is None:
            return None
        return await self._status_message.delete(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._query, name)


def _progress_label(language: str, index: int, total: int) -> str:
    labels = {
        "ar": "⏳ جاري تحميل الرابط {index} من {total} …",
        "en": "⏳ Downloading link {index} of {total} …",
        "tr": "⏳ {total} bağlantının {index}. bağlantısı indiriliyor …",
        "de": "⏳ Link {index} von {total} wird heruntergeladen …",
    }
    return labels.get(language, labels["en"]).format(index=index, total=total)


def install(bot_module) -> None:
    original_message = getattr(bot_module, "handle_message", None)
    original_download = getattr(bot_module, "download_media", None)
    if not callable(original_message) or not callable(original_download):
        return
    if getattr(original_message, "_multi_url_batch", False):
        return

    async def handle_message(update, context):
        message = getattr(update, "message", None)
        text = getattr(message, "text", None) if message else None
        urls = extract_urls(text or "")
        if len(urls) <= 1:
            return await original_message(update, context)

        user = update.effective_user
        language = bot_module.get_language(user.id) or "ar"

        if len(urls) > _MAX_URLS:
            labels = {
                "ar": "❌ الحد الأقصى هو 5 روابط في الرسالة الواحدة.",
                "en": "❌ Maximum 5 links per message.",
                "tr": "❌ Mesaj başına en fazla 5 bağlantı.",
                "de": "❌ Maximal 5 Links pro Nachricht.",
            }
            await message.reply_text(labels.get(language, labels["en"]))
            return

        bot_module.register_user(user)
        if bot_module.is_banned(user.id):
            await message.reply_text(
                bot_module.TEXTS.get(language, bot_module.TEXTS["en"])["banned"]
            )
            return

        valid = []
        for url in urls:
            try:
                bot_module.validate_public_http_url(url)
            except ValueError:
                continue
            valid.append(url)

        if not valid:
            return await original_message(update, context)

        if len(valid) == 1:
            # The original handler expects one URL in message.text. When the
            # user sent several URLs but only one survived validation, pass
            # that clean URL through unchanged to the existing single-URL path.
            single_message = _MessageProxy(message, valid[0])
            single_update = _UpdateProxy(update, message=single_message)
            return await original_message(single_update, context)

        context.user_data["video_urls"] = valid
        # Keep the first URL active while the existing type/quality callback
        # flow runs. Those callbacks validate video_url before download_media
        # is reached; removing it here makes a fresh batch look expired.
        context.user_data["video_url"] = valid[0]
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    bot_module.TEXTS[language]["video_type"],
                    callback_data="video_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    bot_module.TEXTS[language]["audio_type"],
                    callback_data="audio_menu",
                )
            ],
        ])
        labels = {
            "ar": "🔗 تم استلام {n} روابط. سيتم تحميلها جميعًا بعد اختيار النوع:",
            "en": "🔗 Received {n} links. All will be downloaded after you choose the type:",
            "tr": "🔗 {n} bağlantı alındı. Türü seçtikten sonra hepsi indirilecek:",
            "de": "🔗 {n} Links erhalten. Nach Auswahl des Typs werden alle heruntergeladen:",
        }
        await message.reply_text(
            labels.get(language, labels["en"]).format(n=len(valid)),
            reply_markup=keyboard,
        )

    async def download_media(update, context):
        urls = context.user_data.get("video_urls")
        if not isinstance(urls, list) or len(urls) <= 1:
            return await original_download(update, context)

        query = update.callback_query
        user = update.effective_user
        language = bot_module.get_language(user.id) or "ar"

        try:
            for index, url in enumerate(urls, 1):
                context.user_data["video_url"] = url

                if index == 1:
                    active_update = update
                else:
                    status_message = await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=_progress_label(language, index, len(urls)),
                    )
                    active_query = _QueryProxy(query, status_message)
                    active_update = _UpdateProxy(update, query=active_query)

                await original_download(active_update, context)
        finally:
            context.user_data.pop("video_url", None)
            context.user_data.pop("video_urls", None)

    handle_message._multi_url_batch = True
    download_media._multi_url_batch = True
    bot_module.handle_message = handle_message
    bot_module.download_media = download_media
    print("🔗 Multi-URL Batch: ENABLED (up to 5 links per message)", flush=True)


__all__ = ["extract_urls", "install"]
