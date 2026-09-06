"""Non-invasive Telegram Mini App integration for AliBot.

The Mini App sends small, versioned JSON payloads through Telegram's
web_app_data update. This module only bridges those payloads to the existing
bot flow; it does not import or modify downloader internals.
"""

from __future__ import annotations

import json
import os
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
    Update,
)
from telegram.ext import ContextTypes, MessageHandler, filters


ENV_URL = "ALIBOT_MINI_APP_URL"
MAX_PAYLOAD_BYTES = 4096


def _miniapp_url() -> str:
    return os.getenv(ENV_URL, "").strip()


def miniapp_keyboard() -> ReplyKeyboardMarkup | None:
    url = _miniapp_url()
    if not url.startswith("https://"):
        return None
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🌐 فتح AliBot Mini App", web_app=WebAppInfo(url=url))]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="أرسل رابطاً أو افتح Mini App",
    )


def install_message_hooks(bot_module: Any) -> None:
    """Add the Mini App launch button after existing start flows."""
    if not _miniapp_url().startswith("https://"):
        return

    async def _after(original, update, context):
        await original(update, context)
        user = update.effective_user
        message = update.effective_message
        markup = miniapp_keyboard()
        if user and message and markup:
            try:
                await message.reply_text("🌐 يمكنك استخدام الواجهة الاحترافية أيضاً:", reply_markup=markup)
            except Exception:
                pass

    original_start = bot_module.start
    original_start_button = bot_module.start_button_callback
    original_language = bot_module.language_callback

    async def start_wrapper(update, context):
        await _after(original_start, update, context)

    async def start_button_wrapper(update, context):
        await _after(original_start_button, update, context)

    async def language_wrapper(update, context):
        await _after(original_language, update, context)

    bot_module.start = start_wrapper
    bot_module.start_button_callback = start_button_wrapper
    bot_module.language_callback = language_wrapper


async def web_app_data_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    bot_module: Any,
) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.web_app_data:
        return

    raw = message.web_app_data.data or ""
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        await message.reply_text("❌ بيانات Mini App أكبر من الحد المسموح.")
        return

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        await message.reply_text("❌ تعذر قراءة طلب Mini App.")
        return

    if not isinstance(payload, dict) or payload.get("version") != 1:
        await message.reply_text("❌ إصدار طلب Mini App غير مدعوم.")
        return

    if bot_module.is_banned(user.id):
        await message.reply_text(bot_module.TEXTS["ar"]["banned"])
        return

    bot_module.register_user(user)
    language = bot_module.get_language(user.id) or "ar"
    action = str(payload.get("action") or "").strip().lower()

    if action == "download":
        url = str(payload.get("url") or "").strip()
        try:
            bot_module.validate_public_http_url(url)
        except Exception:
            await message.reply_text(bot_module.TEXTS[language]["invalid_url"])
            return

        context.user_data["video_url"] = url
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(bot_module.TEXTS[language]["video_type"], callback_data="video_menu")],
            [InlineKeyboardButton(bot_module.TEXTS[language]["audio_type"], callback_data="audio_menu")],
        ])
        await message.reply_text(
            bot_module.TEXTS[language]["received"],
            reply_markup=keyboard,
        )
        return

    if action == "search":
        query_text = str(payload.get("query") or "").strip()
        if len(query_text) < 2 or len(query_text) > 200:
            await message.reply_text("❌ اكتب عبارة بحث بين حرفين و200 حرف.")
            return

        try:
            from plugins.recovered_features import _youtube_search
            results = await _youtube_search(query_text)
        except Exception:
            await message.reply_text("❌ تعذر تنفيذ البحث الآن. حاول مرة أخرى.")
            return

        if not results:
            await message.reply_text("❌ لم أجد نتائج مناسبة.")
            return

        context.user_data["smart_search_results"] = [
            {"url": item["url"], "title": item["title"]}
            for item in results
        ]
        keyboard = []
        lines = ["🔎 <b>نتائج البحث الذكي</b>", "━━━━━━━━━━━━━━━━━━", ""]
        for index, item in enumerate(results):
            channel = str(item.get("channel") or "").strip()
            title = str(item.get("title") or "").strip()
            meta = f" — {channel[:40]}" if channel else ""
            lines.append(f"{index + 1}. {title[:80]}{meta}")
            keyboard.append([
                InlineKeyboardButton(
                    f"{index + 1}️⃣ {title[:45]}",
                    callback_data=f"smart_search_pick_{index}",
                )
            ])
        lines.append("\n👇 اختر النتيجة التي تريد تحميلها.")
        await message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    await message.reply_text("❌ طلب Mini App غير معروف.")


def register_miniapp(app: Any, bot_module: Any) -> None:
    if not _miniapp_url().startswith("https://"):
        return
    install_message_hooks(bot_module)
    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.WEB_APP_DATA,
            lambda update, context: web_app_data_handler(update, context, bot_module),
            block=True,
        ),
        group=-2,
    )
