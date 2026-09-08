"""Isolated administrative broadcast operations."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

MAX_BROADCAST_LENGTH = 4000


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    if not _authorized(update, owner_id) or not update.message:
        return
    context.user_data["waiting_broadcast"] = True
    await update.message.reply_text(
        "📢 <b>إرسال إعلان</b>\n\nأرسل الآن نص الإعلان الذي تريد إرساله لجميع مستخدمي البوت.\n\n❌ للإلغاء استخدم /cancel",
        parse_mode="HTML",
    )


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    context.user_data["waiting_broadcast"] = True
    await query.edit_message_text(
        "📢 <b>إرسال إعلان</b>\n\nأرسل الآن نص الإعلان.\n\nسيتم إرساله إلى جميع المستخدمين غير المحظورين.\n\n❌ للإلغاء استخدم /cancel",
        parse_mode="HTML",
    )


async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    if not _authorized(update, owner_id) or not update.message:
        return
    if not context.user_data.get("waiting_broadcast"):
        return
    message = (update.message.text or "").strip()
    if not message:
        await update.message.reply_text("❌ الإعلان فارغ.")
        return
    if len(message) > MAX_BROADCAST_LENGTH:
        await update.message.reply_text(f"❌ الحد الأقصى للإعلان {MAX_BROADCAST_LENGTH} حرف.")
        return

    context.user_data["waiting_broadcast"] = False
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO broadcast_logs (admin_id, message, sent_count, failed_count, created_at) VALUES (?, ?, 0, 0, ?)",
            (owner_id, message, datetime.now().isoformat()),
        )
        broadcast_id = cur.lastrowid
        users = cur.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        conn.commit()
    finally:
        conn.close()

    sent = failed = 0
    status = await update.message.reply_text(f"📢 جاري إرسال الإعلان...\n\n👥 المستهدفون: {len(users)}")
    for row in users:
        try:
            sent_message = await context.bot.send_message(chat_id=row["user_id"], text=message)
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO broadcast_messages (broadcast_id, user_id, message_id, created_at) VALUES (?, ?, ?, ?)",
                    (broadcast_id, row["user_id"], sent_message.message_id, datetime.now().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    conn = get_db()
    try:
        conn.execute("UPDATE broadcast_logs SET sent_count = ?, failed_count = ? WHERE id = ?", (sent, failed, broadcast_id))
        conn.commit()
    finally:
        conn.close()
    await status.edit_text(f"✅ انتهى إرسال الإعلان.\n\n📨 تم الإرسال: {sent}\n❌ فشل الإرسال: {failed}\n👥 الإجمالي: {len(users)}")


async def delete_broadcasts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, user_id, message_id FROM broadcast_messages ORDER BY id ASC").fetchall()
    finally:
        conn.close()
    if not rows:
        await query.edit_message_text("🗑️ لا توجد إعلانات محفوظة للحذف.")
        return
    deleted = failed = 0
    status = await query.edit_message_text(f"🗑️ جاري حذف الإعلانات المرسلة...\n\n📨 الرسائل المسجلة: {len(rows)}")
    for row in rows:
        try:
            await context.bot.delete_message(chat_id=row["user_id"], message_id=row["message_id"])
            deleted += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    conn = get_db()
    try:
        conn.execute("DELETE FROM broadcast_messages")
        conn.commit()
    finally:
        conn.close()
    await status.edit_text(
        f"✅ تم الانتهاء من مسح الإعلانات.\n━━━━━━━━━━━━━━━━━━\n\n🗑️ تم حذفها: {deleted}\n⚠️ تعذر حذفها: {failed}\n📨 الإجمالي: {len(rows)}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")]]),
    )


def register_admin_broadcast(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(CommandHandler("broadcast", lambda u, c: broadcast_command(u, c, owner_id)), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: broadcast_callback(u, c, owner_id), pattern=r"^admin_broadcast$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: delete_broadcasts_callback(u, c, get_db, owner_id), pattern=r"^admin_delete_broadcasts$"), group=-1)
