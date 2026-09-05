"""Restored AliBot user-facing features.

Contains two isolated features:
- Smart Search: natural-language YouTube search without AI.
- User Recovery: admin-only re-engagement of inactive users.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
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


URL_RE = re.compile(r"^https?://", re.IGNORECASE)
SEARCH_PICK_RE = re.compile(r"^smart_search_pick_(\d+)$")
RECOVER_DAYS_RE = re.compile(r"^recover_users_(30|60|90)$")


def _lang(bot_module: Any, user_id: int) -> str:
    return bot_module.get_language(user_id) or "ar"


def _looks_like_url(text: str) -> bool:
    return bool(URL_RE.match(text.strip()))


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)


def _score(query: str, entry: dict[str, Any]) -> float:
    title = str(entry.get("title") or "")
    q = _normalize(query).split()
    t = _normalize(title).split()
    if not q or not t:
        return 0.0
    overlap = len(set(q) & set(t)) / max(1, len(set(q)))
    phrase = 1.0 if _normalize(query).strip() in _normalize(title) else 0.0
    duration = entry.get("duration")
    duration_bonus = 0.1 if isinstance(duration, (int, float)) and 1 <= duration <= 1800 else 0.0
    return overlap * 2.0 + phrase + duration_bonus


async def _youtube_search(query: str) -> list[dict[str, Any]]:
    command = [
        "python", "-m", "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--playlist-end", "5",
        f"ytsearch5:{query}",
    ]
    if shutil.which("deno"):
        command[4:4] = ["--js-runtimes", "deno"]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("search_timeout")

    if process.returncode != 0:
        raise RuntimeError((stderr or b"").decode("utf-8", "ignore")[-500:])

    payload = json.loads(stdout.decode("utf-8", "ignore"))
    entries = payload.get("entries") if isinstance(payload, dict) else []
    results = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("webpage_url") or entry.get("url")
        if not url and entry.get("id"):
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        title = str(entry.get("title") or "").strip()
        if not url or not title:
            continue
        item = {
            "url": url,
            "title": title,
            "channel": str(entry.get("channel") or entry.get("uploader") or "").strip(),
            "duration": entry.get("duration"),
        }
        item["score"] = _score(query, item)
        results.append(item)
    results.sort(key=lambda item: (-item["score"], item["title"].lower()))
    return results[:5]


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

    # Admin messages that belong to an existing admin workflow must continue
    # to the original admin router instead of being treated as searches.
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
        results = await _youtube_search(text)
    except Exception as exc:
        print(f"Smart Search error: {exc}", flush=True)
        await status.edit_text("❌ تعذر تنفيذ البحث الآن. حاول مرة أخرى بعد قليل.")
        raise ApplicationHandlerStop

    if not results:
        await status.edit_text("❌ لم أجد نتائج مناسبة. جرّب كلمات بحث مختلفة.")
        raise ApplicationHandlerStop

    context.user_data["smart_search_results"] = [
        {"url": item["url"], "title": item["title"]} for item in results
    ]
    keyboard = []
    lines = ["🔎 <b>نتائج البحث الذكي</b>", "━━━━━━━━━━━━━━━━━━", ""]
    for index, item in enumerate(results):
        meta = []
        if item.get("channel"):
            meta.append(item["channel"][:40])
        duration = _duration(item.get("duration"))
        if duration:
            meta.append(duration)
        suffix = f" — {' • '.join(meta)}" if meta else ""
        lines.append(f"{index + 1}. {item['title'][:80]}{suffix}")
        keyboard.append([
            InlineKeyboardButton(
                f"{index + 1}️⃣ {item['title'][:45]}",
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
    await query.edit_message_text(
        f"🎯 <b>تم اختيار:</b>\n{selected['title'][:200]}\n\nاختر نوع التحميل:",
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
        keyboard = original_admin_keyboard()
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                "🔄 استرداد المستخدمين",
                callback_data="recover_users_menu",
            )
        ])
        return keyboard

    bot_module.admin_keyboard = admin_keyboard_with_recovery

    async def recover_menu_callback(update, context):
        query = update.callback_query
        await query.answer()
        if not update.effective_user or update.effective_user.id != admin_id:
            return
        await recover_users_command(update, context, bot_module, admin_id)

    # Group -1 gives Smart Search priority over the original catch-all text
    # handlers. ApplicationHandlerStop prevents a normal search from reaching
    # handle_message/admin_text_router a second time.
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
