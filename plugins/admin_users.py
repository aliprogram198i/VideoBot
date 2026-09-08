"""Administrative user-management actions kept separate from user history."""

from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _user_keyboard(user_id: int, banned: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 فك الحظر" if banned else "🚫 حظر المستخدم", callback_data=f"unban_{user_id}" if banned else f"ban_{user_id}")],
        [InlineKeyboardButton("📢 إرسال رسالة", callback_data=f"message_user_{user_id}")],
        [InlineKeyboardButton("🗑️ حذف المستخدم", callback_data=f"delete_user_{user_id}")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
    ])


async def _render_user(update: Update, get_db, owner_id: int, user_id: int) -> None:
    query = update.callback_query
    conn = get_db()
    try:
        row = conn.execute("SELECT user_id, username, first_name, last_name, is_banned, downloads, language, country, last_seen FROM users WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        await query.edit_message_text("❌ المستخدم غير موجود.")
        return
    name = html.escape(" ".join(str(p).strip() for p in (row["first_name"], row["last_name"]) if p) or "غير محدد")
    username = html.escape(f"@{row['username']}" if row["username"] else "غير محدد")
    text = (
        "👤 <b>إدارة المستخدم</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 ID: <code>{row['user_id']}</code>\n"
        f"👤 الاسم: {name}\n🔗 المعرف: {username}\n"
        f"📥 التحميلات: {int(row['downloads'] or 0)}\n"
        f"🌍 اللغة: {html.escape(str(row['language'] or 'غير محددة'))}\n"
        f"📍 البلد: {html.escape(str(row['country'] or 'غير محدد'))}\n"
        f"🕒 آخر نشاط: {html.escape(str(row['last_seen'] or 'غير متوفر'))}\n"
        f"🚦 الحالة: {'🚫 محظور' if row['is_banned'] else '🟢 نشط'}"
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=_user_keyboard(user_id, bool(row["is_banned"])))


async def ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer("تم حظر المستخدم.")
    if not _authorized(update, owner_id):
        return
    try:
        user_id = int((query.data or "").split("_", 1)[1])
    except (ValueError, IndexError):
        return
    conn = get_db()
    try:
        conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    await _render_user(update, get_db, owner_id, user_id)


async def unban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer("تم فك الحظر.")
    if not _authorized(update, owner_id):
        return
    try:
        user_id = int((query.data or "").split("_", 1)[1])
    except (ValueError, IndexError):
        return
    conn = get_db()
    try:
        conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    await _render_user(update, get_db, owner_id, user_id)


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    try:
        user_id = int((query.data or "").replace("delete_user_", ""))
    except ValueError:
        return
    await query.edit_message_text(
        "⚠️ <b>تأكيد حذف المستخدم</b>\n\nسيتم حذف معلومات المستخدم وسجل تحميلاته.\n\n❗ لا يمكن التراجع عن هذه العملية.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ نعم، احذف", callback_data=f"confirm_delete_{user_id}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_user_view_{user_id}")],
        ]),
    )


async def confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    try:
        user_id = int((query.data or "").replace("confirm_delete_", ""))
    except ValueError:
        return
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM downloads WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    await query.edit_message_text("✅ تم حذف المستخدم وبياناته بنجاح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")]]))


async def message_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    try:
        user_id = int((query.data or "").replace("message_user_", ""))
    except ValueError:
        return
    context.user_data["message_target"] = user_id
    context.user_data["waiting_user_message"] = True
    await query.edit_message_text(f"📢 رسالة إلى المستخدم\n\n🆔 ID: {user_id}\n\nأرسل الرسالة الآن.\n\n❌ للإلغاء استخدم /cancel")


async def process_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    if not _authorized(update, owner_id) or not update.message or not context.user_data.get("waiting_user_message"):
        return
    target_id = context.user_data.get("message_target")
    context.user_data["waiting_user_message"] = False
    try:
        await context.bot.send_message(chat_id=target_id, text=update.message.text or "")
        await update.message.reply_text("✅ تم إرسال الرسالة بنجاح.")
    except Exception:
        await update.message.reply_text("❌ تعذر إرسال الرسالة.")


async def search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    context.user_data["waiting_admin_search"] = True
    await query.edit_message_text("🔍 <b>البحث عن مستخدم</b>\n\nأرسل Telegram ID أو username أو الاسم.", parse_mode="HTML")


async def process_search(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    if not _authorized(update, owner_id) or not update.message or not context.user_data.get("waiting_admin_search"):
        return
    context.user_data["waiting_admin_search"] = False
    value = (update.message.text or "").strip()
    conn = get_db()
    try:
        if value.startswith("@"):
            row = conn.execute("SELECT user_id FROM users WHERE username = ? LIMIT 1", (value[1:],)).fetchone()
        elif value.isdigit():
            row = conn.execute("SELECT user_id FROM users WHERE user_id = ? LIMIT 1", (int(value),)).fetchone()
        else:
            like = f"%{value}%"
            row = conn.execute("SELECT user_id FROM users WHERE first_name LIKE ? OR last_name LIKE ? LIMIT 1", (like, like)).fetchone()
    finally:
        conn.close()
    if not row:
        await update.message.reply_text("❌ لم يتم العثور على المستخدم.")
        return
    await update.message.reply_text("✅ تم العثور على المستخدم.", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 عرض المستخدم", callback_data=f"admin_user_view_{int(row['user_id'])}")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
    ]))


def register_admin_users(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: ban_callback(u, c, get_db, owner_id), pattern=r"^ban_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: unban_callback(u, c, get_db, owner_id), pattern=r"^unban_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: delete_callback(u, c, owner_id), pattern=r"^delete_user_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_callback(u, c, get_db, owner_id), pattern=r"^confirm_delete_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: message_user_callback(u, c, owner_id), pattern=r"^message_user_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: search_callback(u, c, owner_id), pattern=r"^admin_search$"), group=-1)
