"""Safe admin broadcast extension supporting text, links, photo, video, audio and voice."""

import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

MAX_BROADCAST_LENGTH = 4000


def _kind(message):
    if message.text:
        return "text"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if message.voice:
        return "voice"
    return None


def register_broadcast_media(app, bot_module, admin_id):
    """Install media-aware broadcast handling without touching user data schema."""

    async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        user = update.effective_user
        if not message or not user or user.id != admin_id:
            return
        if not context.user_data.get("waiting_broadcast"):
            return

        kind = _kind(message)
        if kind is None:
            await message.reply_text("❌ أرسل نصًا أو رابطًا، أو صورة، أو فيديو، أو صوتًا/رسالة صوتية.")
            return

        if kind == "text":
            content = (message.text or "").strip()
            if not content:
                await message.reply_text("❌ الإعلان فارغ.")
                return
            if len(content) > MAX_BROADCAST_LENGTH:
                await message.reply_text(f"❌ الحد الأقصى للإعلان {MAX_BROADCAST_LENGTH} حرف.")
                return
            log_message = content
        else:
            caption = (message.caption or "").strip()
            if len(caption) > MAX_BROADCAST_LENGTH:
                await message.reply_text(f"❌ الحد الأقصى لوصف الإعلان {MAX_BROADCAST_LENGTH} حرف.")
                return
            log_message = f"[{kind}] {caption}" if caption else f"[{kind}]"

        context.user_data["waiting_broadcast"] = False

        conn = bot_module.get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO broadcast_logs (admin_id, message, sent_count, failed_count, created_at) VALUES (?, ?, 0, 0, ?)",
                (admin_id, log_message, datetime.now().isoformat()),
            )
            broadcast_id = cur.lastrowid
            users = cur.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
            conn.commit()
        finally:
            conn.close()

        status_message = await message.reply_text(
            "📢 جاري إرسال الإعلان...\n\n"
            f"📦 النوع: {kind}\n"
            f"👥 المستهدفون: {len(users)}"
        )

        sent = 0
        failed = 0
        for row in users:
            try:
                sent_message = await context.bot.copy_message(
                    chat_id=row["user_id"],
                    from_chat_id=message.chat_id,
                    message_id=message.message_id,
                )
                conn_save = bot_module.get_db()
                try:
                    conn_save.execute(
                        "INSERT INTO broadcast_messages (broadcast_id, user_id, message_id, created_at) VALUES (?, ?, ?, ?)",
                        (broadcast_id, row["user_id"], sent_message.message_id, datetime.now().isoformat()),
                    )
                    conn_save.commit()
                finally:
                    conn_save.close()
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as exc:
                print(f"Broadcast error {row['user_id']}: {exc}", flush=True)
                failed += 1

        conn = bot_module.get_db()
        try:
            conn.execute("UPDATE broadcast_logs SET sent_count = ?, failed_count = ? WHERE id = ?", (sent, failed, broadcast_id))
            conn.commit()
        finally:
            conn.close()

        await status_message.edit_text(
            "✅ انتهى إرسال الإعلان.\n\n"
            f"📨 تم الإرسال: {sent}\n"
            f"❌ فشل الإرسال: {failed}\n"
            f"👥 الإجمالي: {len(users)}"
        )

    bot_module.process_broadcast = process_broadcast

    async def broadcast_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != admin_id:
            return
        context.user_data["waiting_broadcast"] = True
        await update.effective_message.reply_text(
            "📢 إرسال إعلان\n\n"
            "أرسل الآن أحد الأنواع التالية:\n"
            "• 📝 نص\n• 🔗 رابط\n• 🖼️ صورة\n• 🎥 فيديو\n• 🎵 صوت أو رسالة صوتية\n\n"
            "يمكنك إضافة Caption للصورة أو الفيديو أو الصوت.\n\n❌ للإلغاء استخدم /cancel"
        )
        raise ApplicationHandlerStop

    async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not update.effective_user or update.effective_user.id != admin_id:
            return
        context.user_data["waiting_broadcast"] = True
        await query.edit_message_text(
            "📢 إرسال إعلان\n\n"
            "أرسل الآن نصًا أو رابطًا أو صورة أو فيديو أو صوتًا/رسالة صوتية.\n"
            "يمكنك إضافة Caption للوسائط.\n\n❌ للإلغاء استخدم /cancel"
        )
        raise ApplicationHandlerStop

    async def media_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != admin_id:
            return
        if not context.user_data.get("waiting_broadcast"):
            return
        await process_broadcast(update, context)
        raise ApplicationHandlerStop

    app.add_handler(CommandHandler("broadcast", broadcast_prompt), group=-10)
    app.add_handler(CallbackQueryHandler(broadcast_callback, pattern=r"^admin_broadcast$"), group=-10)
    app.add_handler(
        MessageHandler(
            filters.User(admin_id) & (filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE),
            media_router,
        ),
        group=-10,
    )
