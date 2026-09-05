"""Safe scheduled group-broadcast extension.

Features:
- Owner registers groups explicitly with /group_add inside each group.
- Owner configures one persistent campaign from private chat with /group_broadcast.
- Supports text, links, photo, video, audio and voice templates.
- Uses Telegram file_id / text instead of downloading media.
- Stores only campaign/group metadata; existing user/download data is untouched.
- Persistent next-send time is recalculated in SQLite, so schedules survive restarts.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from telegram import Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    TypeHandler,
    filters,
)

TABLE_GROUPS = "group_broadcast_groups"
TABLE_CAMPAIGN = "group_broadcast_campaign"
WAIT_INTERVAL = "waiting_group_broadcast_interval"
WAIT_CONTENT = "waiting_group_broadcast_content"
INTERVAL_RE = re.compile(r"^\s*(\d+)\s*(m|h|d)\s*$", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now()


def _ensure_tables(bot_module: Any) -> None:
    conn = bot_module.get_db()
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_GROUPS} (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_CAMPAIGN} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                interval_seconds INTEGER NOT NULL DEFAULT 432000,
                content_type TEXT,
                text TEXT,
                file_id TEXT,
                caption TEXT,
                next_send_at TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _parse_interval(value: str) -> int | None:
    match = INTERVAL_RE.match(value or "")
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if amount <= 0:
        return None
    seconds = amount * {"m": 60, "h": 3600, "d": 86400}[unit]
    if seconds < 300 or seconds > 365 * 86400:
        return None
    return seconds


def _extract_content(message):
    if message.text:
        return {"content_type": "text", "text": message.text, "file_id": None, "caption": None}
    if message.photo:
        return {"content_type": "photo", "text": None, "file_id": message.photo[-1].file_id, "caption": message.caption or ""}
    if message.video:
        return {"content_type": "video", "text": None, "file_id": message.video.file_id, "caption": message.caption or ""}
    if message.audio:
        return {"content_type": "audio", "text": None, "file_id": message.audio.file_id, "caption": message.caption or ""}
    if message.voice:
        return {"content_type": "voice", "text": None, "file_id": message.voice.file_id, "caption": message.caption or ""}
    return None


async def _send_template(bot, chat_id: int, campaign: dict[str, Any]) -> None:
    kind = campaign["content_type"]
    if kind == "text":
        await bot.send_message(chat_id=chat_id, text=campaign["text"])
    elif kind == "photo":
        await bot.send_photo(chat_id=chat_id, photo=campaign["file_id"], caption=campaign["caption"] or None)
    elif kind == "video":
        await bot.send_video(chat_id=chat_id, video=campaign["file_id"], caption=campaign["caption"] or None)
    elif kind == "audio":
        await bot.send_audio(chat_id=chat_id, audio=campaign["file_id"], caption=campaign["caption"] or None)
    elif kind == "voice":
        await bot.send_voice(chat_id=chat_id, voice=campaign["file_id"], caption=campaign["caption"] or None)


async def _scheduler_tick(bot, bot_module: Any) -> None:
    _ensure_tables(bot_module)
    conn = bot_module.get_db()
    try:
        row = conn.execute(f"SELECT * FROM {TABLE_CAMPAIGN} WHERE id = 1 AND enabled = 1").fetchone()
        groups = conn.execute(f"SELECT chat_id FROM {TABLE_GROUPS} WHERE enabled = 1 ORDER BY chat_id").fetchall()
    finally:
        conn.close()
    if not row or not groups or not row["next_send_at"]:
        return
    try:
        due_at = datetime.fromisoformat(row["next_send_at"])
    except Exception:
        due_at = _now()
    if _now() < due_at:
        return
    sent = 0
    failed = 0
    for group in groups:
        try:
            await _send_template(bot, group["chat_id"], row)
            sent += 1
        except Exception as exc:
            failed += 1
            print(f"Group broadcast error {group['chat_id']}: {exc}", flush=True)
        await asyncio.sleep(1.0)
    next_send = _now() + timedelta(seconds=int(row["interval_seconds"]))
    conn = bot_module.get_db()
    try:
        conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET next_send_at = ?, updated_at = ? WHERE id = 1", (next_send.isoformat(), _now().isoformat()))
        conn.commit()
    finally:
        conn.close()
    print(f"📢 Group broadcast cycle finished: sent={sent}, failed={failed}, next={next_send.isoformat()}", flush=True)


async def _scheduler_loop(application, bot_module: Any) -> None:
    while True:
        try:
            await _scheduler_tick(application.bot, bot_module)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Group broadcast scheduler error: {exc}", flush=True)
        await asyncio.sleep(60)


def register_group_broadcast(app: Any, bot_module: Any, admin_id: int) -> None:
    _ensure_tables(bot_module)
    scheduler_started = False

    async def bootstrap_scheduler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        nonlocal scheduler_started
        if not scheduler_started:
            scheduler_started = True
            context.application.create_task(_scheduler_loop(context.application, bot_module), update=update)

    async def group_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        chat = update.effective_chat
        if not user or user.id != admin_id or not chat or chat.type not in ("group", "supergroup"):
            return
        conn = bot_module.get_db()
        try:
            existing = conn.execute(f"SELECT chat_id FROM {TABLE_GROUPS} WHERE chat_id = ?", (chat.id,)).fetchone()
            now = _now().isoformat()
            conn.execute(f"""
                INSERT INTO {TABLE_GROUPS} (chat_id, title, enabled, added_at, last_seen)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, enabled=1, last_seen=excluded.last_seen
            """, (chat.id, chat.title or "", now, now))
            conn.commit()
        finally:
            conn.close()
        await update.message.reply_text(("✅ تم تحديث المجموعة ضمن قائمة الإعلانات الدورية." if existing else "✅ تمت إضافة هذه المجموعة للإعلانات الدورية.") + "\n\nيمكنك الآن ضبط الإعلان من الخاص عبر /group_broadcast")
        raise ApplicationHandlerStop

    async def group_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        chat = update.effective_chat
        if not user or user.id != admin_id or not chat or chat.type not in ("group", "supergroup"):
            return
        conn = bot_module.get_db()
        try:
            conn.execute(f"UPDATE {TABLE_GROUPS} SET enabled = 0, last_seen = ? WHERE chat_id = ?", (_now().isoformat(), chat.id))
            conn.commit()
        finally:
            conn.close()
        await update.message.reply_text("🛑 تم إيقاف الإعلانات الدورية لهذه المجموعة.")
        raise ApplicationHandlerStop

    async def start_wizard(update, context):
        if not update.effective_user or update.effective_user.id != admin_id or not update.effective_chat or update.effective_chat.type != "private":
            return
        context.user_data[WAIT_INTERVAL] = True
        context.user_data[WAIT_CONTENT] = False
        await update.effective_message.reply_text(
            "📢 إعداد إعلان المجموعات\n\nأرسل مدة التكرار، مثل:\n"
            "• 30m = كل 30 دقيقة\n• 6h = كل 6 ساعات\n• 1d = كل يوم\n• 5d = كل 5 أيام\n\n"
            "الحد الأدنى 5 دقائق والحد الأقصى سنة."
        )
        raise ApplicationHandlerStop

    async def group_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await start_wizard(update, context)

    async def group_broadcast_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await start_wizard(update, context)

    async def group_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != admin_id or not update.effective_chat or update.effective_chat.type != "private":
            return
        if not context.user_data.get(WAIT_CONTENT):
            return
        content = _extract_content(update.effective_message)
        if not content:
            await update.effective_message.reply_text("❌ أرسل نصًا أو رابطًا أو صورة أو فيديو أو صوتًا/رسالة صوتية.")
            return
        context.user_data[WAIT_CONTENT] = False
        interval = int(context.user_data.pop("group_broadcast_interval", 432000))
        now = _now()
        next_send = now + timedelta(seconds=interval)
        conn = bot_module.get_db()
        try:
            conn.execute(f"""
                INSERT INTO {TABLE_CAMPAIGN} (id, enabled, interval_seconds, content_type, text, file_id, caption, next_send_at, updated_at)
                VALUES (1, 1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET enabled=1, interval_seconds=excluded.interval_seconds, content_type=excluded.content_type, text=excluded.text, file_id=excluded.file_id, caption=excluded.caption, next_send_at=excluded.next_send_at, updated_at=excluded.updated_at
            """, (interval, content["content_type"], content["text"], content["file_id"], content["caption"], next_send.isoformat(), now.isoformat()))
            group_count = conn.execute(f"SELECT COUNT(*) FROM {TABLE_GROUPS} WHERE enabled = 1").fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        await update.effective_message.reply_text(
            "✅ تم حفظ وتشغيل إعلان المجموعات.\n\n"
            f"📦 النوع: {content['content_type']}\n👥 المجموعات النشطة: {group_count}\n"
            f"⏱️ التكرار: {_format_interval(interval)}\n🕒 أول إرسال: {next_send.strftime('%Y-%m-%d %H:%M')}\n\n"
            "لن تتأثر بيانات المستخدمين أو التحميلات."
        )
        raise ApplicationHandlerStop

    async def group_broadcast_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != admin_id or not update.effective_chat or update.effective_chat.type != "private":
            return
        if not context.user_data.get(WAIT_INTERVAL):
            return
        seconds = _parse_interval(update.effective_message.text or "")
        if seconds is None:
            await update.effective_message.reply_text("❌ مدة غير صحيحة. استخدم مثلًا 30m أو 6h أو 1d أو 5d.")
            return
        context.user_data[WAIT_INTERVAL] = False
        context.user_data[WAIT_CONTENT] = True
        context.user_data["group_broadcast_interval"] = seconds
        await update.effective_message.reply_text("✅ تم تحديد المدة: " + _format_interval(seconds) + "\n\nالآن أرسل محتوى الإعلان:\n📝 نص\n🔗 رابط\n🖼️ صورة\n🎥 فيديو\n🎵 صوت/رسالة صوتية\n\nيمكنك وضع Caption مع الصورة أو الفيديو أو الصوت.")
        raise ApplicationHandlerStop

    async def group_broadcast_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != admin_id or not update.effective_chat or update.effective_chat.type != "private":
            return
        context.user_data[WAIT_INTERVAL] = False
        context.user_data[WAIT_CONTENT] = False
        conn = bot_module.get_db()
        try:
            conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET enabled = 0, updated_at = ? WHERE id = 1", (_now().isoformat(),))
            conn.commit()
        finally:
            conn.close()
        await update.effective_message.reply_text("🛑 تم إيقاف إعلان المجموعات. لم يتم حذف أي بيانات.")
        raise ApplicationHandlerStop

    async def group_broadcast_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != admin_id or not update.effective_chat or update.effective_chat.type != "private":
            return
        conn = bot_module.get_db()
        try:
            campaign = conn.execute(f"SELECT * FROM {TABLE_CAMPAIGN} WHERE id = 1").fetchone()
            count = conn.execute(f"SELECT COUNT(*) FROM {TABLE_GROUPS} WHERE enabled = 1").fetchone()[0]
        finally:
            conn.close()
        if not campaign or not campaign["enabled"]:
            await update.effective_message.reply_text(f"📊 إعلان المجموعات: متوقف\n👥 المجموعات المسجلة: {count}")
        else:
            await update.effective_message.reply_text("📊 حالة إعلان المجموعات\n\n🟢 الحالة: يعمل\n" f"👥 المجموعات: {count}\n📦 النوع: {campaign['content_type']}\n" f"⏱️ التكرار: {_format_interval(int(campaign['interval_seconds']))}\n🕒 الإرسال القادم: {campaign['next_send_at']}")
        raise ApplicationHandlerStop

    app.add_handler(TypeHandler(Update, bootstrap_scheduler), group=-20)
    app.add_handler(CommandHandler("group_add", group_add), group=-10)
    app.add_handler(CommandHandler("group_remove", group_remove), group=-10)
    app.add_handler(CommandHandler("group_broadcast", group_broadcast_command), group=-10)
    app.add_handler(CommandHandler("group_broadcast_stop", group_broadcast_stop), group=-10)
    app.add_handler(CommandHandler("group_broadcast_status", group_broadcast_status), group=-10)
    app.add_handler(CallbackQueryHandler(group_broadcast_panel, pattern=r"^group_broadcast_panel$", block=True), group=-10)
    app.add_handler(MessageHandler(filters.User(admin_id) & filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, group_broadcast_interval), group=-10)
    app.add_handler(MessageHandler(filters.User(admin_id) & filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE) & ~filters.COMMAND, group_broadcast_content), group=-9)


def _format_interval(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"كل {seconds // 86400} يوم"
    if seconds % 3600 == 0:
        return f"كل {seconds // 3600} ساعة"
    return f"كل {seconds // 60} دقيقة"
