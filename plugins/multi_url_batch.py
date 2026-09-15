"""Fail-open multi-URL batch adapter for AliBot."""
from __future__ import annotations

import inspect
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


class _QueryProxy:
    """Keep the original callback data while suppressing edits after item one."""
    def __init__(self, query):
        self._query = query
        self.data = query.data
        self.from_user = query.from_user
        self.message = query.message

    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_text(self, *args, **kwargs):
        return None

    async def delete_message(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self._query, name)


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
        if len(urls) > _MAX_URLS:
            user = getattr(update, "effective_user", None)
            language = bot_module.get_language(user.id) if user else "ar"
            language = language or "ar"
            await message.reply_text(
                {"ar":"❌ الحد الأقصى هو 5 روابط في الرسالة الواحدة.","en":"❌ Maximum 5 links per message.","tr":"❌ Mesaj başına en fazla 5 bağlantı.","de":"❌ Maximal 5 Links pro Nachricht."}.get(language, "❌ Maximum 5 links per message.")
            return

        user = update.effective_user
        bot_module.register_user(user)
        if bot_module.is_banned(user.id):
            await message.reply_text(bot_module.TEXTS["ar"]["banned"])
            return
        language = bot_module.get_language(user.id) or "ar"
        valid = []
        for url in urls:
            try:
                bot_module.validate_public_http_url(url)
            except ValueError:
                continue
            valid.append(url)
        if len(valid) <= 1:
            return await original_message(update, context)
        context.user_data["video_urls"] = valid
        context.user_data.pop("video_url", None)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(bot_module.TEXTS[language]["video_type"], callback_data="video_menu")],
            [InlineKeyboardButton(bot_module.TEXTS[language]["audio_type"], callback_data="audio_menu")],
        ])
        labels = {"ar":"🔗 تم استلام {n} روابط. سيتم تحميلها جميعًا بعد اختيار النوع:","en":"🔗 Received {n} links. All will be downloaded after you choose the type:","tr":"🔗 {n} bağlantı alındı. Türü seçtikten sonra hepsi indirilecek:","de":"🔗 {n} Links erhalten. Nach Auswahl des Typs werden alle heruntergeladen:"}
        await message.reply_text(labels.get(language, labels["en"]).format(n=len(valid)), reply_markup=keyboard)

    async def download_media(update, context):
        urls = context.user_data.get("video_urls")
        if not isinstance(urls, list) or len(urls) <= 1:
            return await original_download(update, context)
        query = update.callback_query
        for index, url in enumerate(urls, 1):
            context.user_data["video_url"] = url
            active_update = update if index == 1 else _UpdateProxy(update, _QueryProxy(query))
            if index > 1:
                await context.bot.send_message(update.effective_chat.id, f"⏳ جاري تحميل الرابط {index} من {len(urls)} …")
            await original_download(active_update, context)
        context.user_data.pop("video_url", None)
        context.user_data.pop("video_urls", None)

    class _UpdateProxy:
        def __init__(self, update, query):
            self._update = update
            self.callback_query = query
        def __getattr__(self, name):
            return getattr(self._update, name)

    handle_message._multi_url_batch = True
    download_media._multi_url_batch = True
    bot_module.handle_message = handle_message
    bot_module.download_media = download_media
    print("🔗 Multi-URL Batch: ENABLED (up to 5 links per message)", flush=True)


__all__ = ["extract_urls", "install"]
