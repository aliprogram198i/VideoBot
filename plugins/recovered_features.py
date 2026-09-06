"""Restored AliBot user-facing features.

Contains two isolated features:
- Smart Search: deterministic YouTube search/ranking without AI.
- User Recovery: admin-only re-engagement of inactive users.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from plugins.smart_search_engine import search_youtube


URL_RE = re.compile(r"^https?://", re.IGNORECASE)
SEARCH_PICK_RE = re.compile(r"^smart_search_pick_(\d+)$")
RECOVER_DAYS_RE = re.compile(r"^recover_users_(30|60|90)$")


def _lang(bot_module: Any, user_id: int) -> str:
    return bot_module.get_language(user_id) or "ar"


def _looks_like_url(text: str) -> bool:
    return bool(URL_RE.match(text.strip()))


def _duration(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    total = int(value)
    return f"{total // 60}:{total % 60:02d}"


async def smart_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    if not update.message or not update.effective_user:
        return
    text = (update.message.text or "").strip()
    if not text or _looks_like_url(text) or text.startswith("/"):
        return

    user = update.effective_user

    if user.id == bot_module.ADMIN_ID and any(
        context.user_data.get(key)
        for key in ("waiting_broadcast", "waiting_user_message", "waiting_admin_search")
    ):
        return

    bot_module.register_user(user)
    if bot_module.is_banned(user.id):
        await update.message.reply_text(bot_module.TEXTS["ar"]["banned"])
        raise ApplicationHandlerStop

    language = _lang(bot_module, user.id)
    if not bot_module.get_language(user.id):
        await update.message.reply_text(
            bot_module.TEXTS["ar"]["choose_language"],
            reply_markup=bot_module.language_keyboard(),
        )
        raise ApplicationHandlerStop

    if len(text) < 2 or len(text) > 200:
        await update.message.reply_text("❌ اكتب عبارة بحث بين حرفين و200 حرف.")
        raise ApplicationHandlerStop

    status = await update.message.reply_text(
        "🔎 جاري البحث الذكي...\n\nبدون AI — يتم ترتيب النتائج خوارزميًا."
    )
    try:
        results = await search_youtube(text)
    except Exception as exc:
        print(f"Smart Search error: {exc}", flush=True)
        await status.edit_text("❌ تعذر تنفيذ البحث الآن. حاول مرة أخرى بعد قليل.")
        raise ApplicationHandlerStop

    if not results:
        await status.edit_text("❌ لم أجد نتائج مناسبة. جرّب كلمات بحث مختلفة.")
        raise ApplicationHandlerStop

    context.user_data["smart_search_results"] = [
        {
            "url": item["url"],
            "title": item["title"],
            "channel": item.get("channel", ""),
            "duration": item.get("duration"),
            "score": item.get("score", 0.0),
        }
        for item in results
    ]

    keyboard = []
    lines = ["🔎 <b>نتائج البحث الذكي</b>", "━━━━━━━━━━━━━━━━━━", ""]
    for index, item in enumerate(results):
        title = html.escape(str(item["title"])[:80])
        meta = []
        if item.get("channel"):
            meta.append(html.escape(str(item["channel"])[:40]))
        duration = _duration(item.get("duration"))
        if duration:
            meta.append(duration)
        suffix = f" — {' • '.join(meta)}" if meta else ""
        lines.append(f"{index + 1}. {title}{suffix}")
        button_title = str(item["title"]).replace("\n", " ").strip()[:45]
        keyboard.append([
            InlineKeyboardButton(
                f"{index + 1}️⃣ {button_title}",
                callback_data=f"smart_search_pick_{index}",
            )
        ])
    lines.append("\n👇 اختر النتيجة التي تريد تحميلها.")
    await status.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    raise ApplicationHandlerStop


async def smart_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if not user:
        return
    match = SEARCH_PICK_RE.match(query.data or "")
    if not match:
        return
    index = int(match.group(1))
    results = context.user_data.get("smart_search_results") or []
    if index < 0 or index >= len(results):
        await query.edit_message_text("❌ انتهت صلاحية نتائج البحث. أعد البحث من جديد.")
        return
    selected = results[index]
    try:
        bot_module.validate_public_http_url(selected["url"])
    except Exception:
        await query.edit_message_text("❌ تعذر التحقق من نتيجة البحث.")
        return
    context.user_data["video_url"] = selected["url"]
    language = _lang(bot_module, user.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(bot_module.TEXTS[language]["video_type"], callback_data="video_menu")],
        [InlineKeyboardButton(bot_module.TEXTS[language]["audio_type"], callback_data="audio_menu")],
        [InlineKeyboardButton(bot_module.TEXTS[language]["back"], callback_data="main_menu")],
    ])
    safe_title = html.escape(str(selected["title"])[:200])
    await query.edit_message_text(
        f"🎯 <b>تم اختيار:</b>\n{safe_title}\n\nاختر نوع التحميل:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def recover_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any, admin_id: int) -> None:
    if not update.effective_user or update.effective_user.id != admin_id:
        return
    conn = bot_module.get_db()
    rows = []
    try:
        for days in (30, 60, 90):
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            row = conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_banned = 0 AND user_id != ? AND last_seen IS NOT NULL AND last_seen < ?",
                (admin_id, cutoff),
            ).fetchone()
            rows.append(int(row[0] or 0))
    finally:
        conn.close()
    text = (
        "🔄 <b>استرداد المستخدمين غير النشطين</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🟡 أكثر من 30 يوم: {rows[0]}\n"
        f"🟠 أكثر من 60 يوم: {rows[1]}\n"
        f"🔴 أكثر من 90 يوم: {rows[2]}\n\n"
        "اختر الفئة لإرسال رسالة تذكير.\n"
        "لن يتم حذف أي مستخدم أو تعديل بياناته."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟡 استرداد +30 يوم", callback_data="recover_users_30")],
        [InlineKeyboardButton("🟠 استرداد +60 يوم", callback_data="recover_users_60")],
        [InlineKeyboardButton("🔴 استرداد +90 يوم", callback_data="recover_users_90")],
        [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_home")],
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def recover_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any, admin_id: int) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if not user or user.id != admin_id:
        return
    match = RECOVER_DAYS_RE.match(query.data or "")
    if not match:
        return
    days = int(match.group(1))
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = bot_module.get_db()
    try:
        rows = conn.execute(
            "SELECT user_id FROM users WHERE is_banned = 0 AND user_id != ? AND last_seen IS NOT NULL AND last_seen < ? ORDER BY last_seen ASC LIMIT 500",
            (admin_id, cutoff),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        await query.edit_message_text("🟢 لا يوجد مستخدمون ضمن هذه الفئة حاليًا.")
        return

    await query.edit_message_text(
        f"⏳ جاري محاولة استرداد {len(rows)} مستخدم...\n\n"
        "سيتم تجاهل الحسابات التي لم يعد البوت قادرًا على مراسلتها."
    )
    sent = 0
    failed = 0
    message = (
        "👋 نفتقدك في AliBot!\n\n"
        "🚀 يمكنك الآن إرسال رابط فيديو أو كتابة اسم فيديو/أغنية مباشرة للبحث الذكي.\n\n"
        "🎬 حمّل ما تريد بسهولة ومجانًا."
    )
    for row in rows:
        try:
            await context.bot.send_message(chat_id=row["user_id"], text=message)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.08)
    await query.edit_message_text(
        "✅ انتهت محاولة الاسترداد.\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📨 تم التواصل بنجاح: {sent}\n"
        f"⚠️ تعذر التواصل: {failed}\n"
        f"👥 الفئة المستهدفة: {len(rows)}"
    )


def register_recovered_features(app: Any, bot_module: Any, admin_id: int) -> None:
    """Register restored features through an isolated bootstrap hook."""
    original_admin_keyboard = bot_module.admin_keyboard

    def admin_keyboard_with_recovery():
        original_markup = original_admin_keyboard()
        keyboard = [list(row) for row in original_markup.inline_keyboard]
        keyboard.append([
            InlineKeyboardButton(
                "🔄 استرداد المستخدمين",
                callback_data="recover_users_menu",
            )
        ])
        return InlineKeyboardMarkup(keyboard)

    bot_module.admin_keyboard = admin_keyboard_with_recovery

    async def recover_menu_callback(update, context):
        query = update.callback_query
        await query.answer()
        if not update.effective_user or update.effective_user.id != admin_id:
            return
        await recover_users_command(update, context, bot_module, admin_id)

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            lambda update, context: smart_search_handler(update, context, bot_module),
        ),
        group=-1,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: smart_search_pick(update, context, bot_module),
            pattern=r"^smart_search_pick_\d+$",
            block=False,
        ),
    )
    app.add_handler(
        CommandHandler(
            "recover_users",
            lambda update, context: recover_users_command(update, context, bot_module, admin_id),
        ),
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: recover_users_callback(update, context, bot_module, admin_id),
            pattern=r"^recover_users_(30|60|90)$",
        ),
    )
    app.add_handler(
        CallbackQueryHandler(
            recover_menu_callback,
            pattern=r"^recover_users_menu$",
        ),
    )
    print("🧩 Restored features: Smart Search + User Recovery ENABLED", flush=True)
