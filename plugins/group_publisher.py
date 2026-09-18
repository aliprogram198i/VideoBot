"""AliBot authorized group publisher.

A group becomes known to AliBot only after an administrator explicitly adds
the bot to that group and accepts the publisher consent screen. Telegram does
not expose a user's complete group list to bots, so there is no silent group
discovery.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)

CALLBACK = "group_publisher"
CONSENT_PREFIX = "group_consent_"
PUBLISH_PREFIX = "group_publish_"
REMOVE_PREFIX = "group_remove_"
ADMIN_CALLBACK = "admin_group_publisher"
WAITING_KEY = "group_publisher_waiting_message"
TARGET_KEY = "group_publisher_target"
ADD_GROUP_URL = "https://t.me/MyVideoDownloaderAliBot?startgroup=publisher"


def ensure_schema(get_db) -> None:
    conn = get_db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_groups (
            chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            owner_user_id INTEGER NOT NULL, owner_username TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.commit()
    finally:
        conn.close()


def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")


async def _is_group_admin(context, chat_id: int, user_id: int) -> bool:
    member = await context.bot.get_chat_member(chat_id, user_id)
    return member.status in {"creator", "administrator"}


async def _bot_can_publish(context, chat_id: int) -> bool:
    me = await context.bot.get_me()
    member = await context.bot.get_chat_member(chat_id, me.id)
    return member.status in {"creator", "administrator", "member"}


def _list_groups(get_db, owner_user_id: int | None = None):
    conn = get_db()
    try:
        if owner_user_id is None:
            return conn.execute("SELECT chat_id,title,owner_user_id,owner_username,created_at,updated_at FROM bot_groups ORDER BY title COLLATE NOCASE,chat_id").fetchall()
        return conn.execute("SELECT chat_id,title,owner_username FROM bot_groups WHERE owner_user_id=? ORDER BY title COLLATE NOCASE,chat_id", (owner_user_id,)).fetchall()
    finally:
        conn.close()


def _get_group(get_db, chat_id: int):
    conn = get_db()
    try:
        return conn.execute("SELECT chat_id,title,owner_user_id,owner_username FROM bot_groups WHERE chat_id=?", (chat_id,)).fetchone()
    finally:
        conn.close()


def _upsert_group(get_db, chat_id: int, title: str, owner) -> None:
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        conn.execute("""INSERT INTO bot_groups(chat_id,title,owner_user_id,owner_username,created_at,updated_at)
                        VALUES(?,?,?,?,?,?)
                        ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,updated_at=excluded.updated_at""",
                     (chat_id, title[:255], owner.id, owner.username, now, now))
        conn.commit()
    finally:
        conn.close()


def _delete_group(get_db, chat_id: int, owner_user_id: int | None = None) -> bool:
    conn = get_db()
    try:
        if owner_user_id is None:
            cur = conn.execute("DELETE FROM bot_groups WHERE chat_id=?", (chat_id,))
        else:
            cur = conn.execute("DELETE FROM bot_groups WHERE chat_id=? AND owner_user_id=?", (chat_id, owner_user_id))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ أوافق وأريد ربط مجموعة", callback_data=f"{CONSENT_PREFIX}yes")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"{CONSENT_PREFIX}no")],
    ])


def _add_group_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("➕ اختيار مجموعة وإضافة AliBot", url=ADD_GROUP_URL)]])


def _groups_keyboard(rows) -> InlineKeyboardMarkup:
    buttons = []
    for row in rows:
        chat_id = int(row["chat_id"])
        title = str(row["title"] or "مجموعة")[:28]
        buttons.append([
            InlineKeyboardButton(f"📢 {title}", callback_data=f"{PUBLISH_PREFIX}{chat_id}"),
            InlineKeyboardButton("🗑️", callback_data=f"{REMOVE_PREFIX}{chat_id}"),
        ])
    buttons.append([InlineKeyboardButton("➕ إضافة مجموعة", url=ADD_GROUP_URL)])
    return InlineKeyboardMarkup(buttons)


async def groups_command(update: Update, context, get_db):
    if not _is_private(update) or not update.effective_user:
        return
    ensure_schema(get_db)
    await update.effective_message.reply_text(
        "👥 <b>ربط وإدارة المجموعات</b>\n\n"
        "🔐 لا يصل AliBot إلى قائمة مجموعاتك تلقائيًا.\n"
        "أنت تختار المجموعة بنفسك، وتضيف AliBot إليها بإرادتك.\n\n"
        "بعد الإضافة، لا يتم تفعيل النشر إلا بعد التحقق من أنك مدير المجموعة.\n\n"
        "يمكنك إلغاء الربط في أي وقت.",
        parse_mode="HTML", reply_markup=_consent_keyboard())


async def group_publisher_callback(update: Update, context, get_db):
    query = update.callback_query
    await query.answer()
    if not query.from_user or not _is_private(update):
        return
    ensure_schema(get_db)
    data = query.data or ""

    if data == CALLBACK:
        rows = _list_groups(get_db, query.from_user.id)
        await query.edit_message_text(
            "👥 <b>مجموعاتك المرتبطة</b>\n\n" +
            ("لا توجد مجموعات مرتبطة بعد." if not rows else "اختر مجموعة للنشر أو احذف الربط."),
            parse_mode="HTML", reply_markup=_groups_keyboard(rows) if rows else _consent_keyboard())
        return

    if data == f"{CONSENT_PREFIX}yes":
        await query.edit_message_text(
            "🔐 <b>موافقة ربط المجموعة</b>\n\n"
            "بالضغط على «اختيار مجموعة» ستفتح Telegram لاختيار المجموعة التي تريد إضافة AliBot إليها. "
            "لن يتم ربط أي مجموعة أخرى، ولن يتم النشر فيها دون طلب منك.",
            parse_mode="HTML", reply_markup=_add_group_keyboard())
        return
    if data == f"{CONSENT_PREFIX}no":
        await query.edit_message_text("تم الإلغاء. لم يتم ربط أي مجموعة.")
        return

    try:
        if data.startswith(PUBLISH_PREFIX):
            chat_id = int(data[len(PUBLISH_PREFIX):])
            row = _get_group(get_db, chat_id)
            if not row or int(row["owner_user_id"]) != query.from_user.id:
                await query.edit_message_text("❌ هذه المجموعة غير مرتبطة بحسابك.")
                return
            if not await _is_group_admin(context, chat_id, query.from_user.id):
                _delete_group(get_db, chat_id, query.from_user.id)
                await query.edit_message_text("⚠️ لم تعد تملك صلاحية إدارة هذه المجموعة، لذلك أُلغي الربط تلقائيًا.")
                return
            if not await _bot_can_publish(context, chat_id):
                await query.edit_message_text("❌ لا يستطيع AliBot إرسال الرسائل إلى هذه المجموعة حاليًا.")
                return
            context.user_data[WAITING_KEY] = True
            context.user_data[TARGET_KEY] = chat_id
            await query.edit_message_text(
                "📢 <b>إرسال رسالة إلى المجموعة</b>\n\n"
                f"👥 {html.escape(str(row['title'] or 'المجموعة'))}\n\n"
                "أرسل نص الرسالة الآن.\n❌ للإلغاء: /cancel", parse_mode="HTML")
            return
        if data.startswith(REMOVE_PREFIX):
            chat_id = int(data[len(REMOVE_PREFIX):])
            if _delete_group(get_db, chat_id, query.from_user.id):
                await query.edit_message_text("✅ تم إلغاء ربط المجموعة.")
            else:
                await query.edit_message_text("❌ تعذر إلغاء الربط.")
    except (TypeError, ValueError):
        await query.edit_message_text("❌ طلب غير صالح.")


async def process_group_publisher_message(update, context, get_db) -> bool:
    if not update.effective_user or not update.message or not _is_private(update):
        return False
    if not context.user_data.get(WAITING_KEY):
        return False
    chat_id = context.user_data.get(TARGET_KEY)
    text = update.message.text
    context.user_data[WAITING_KEY] = False
    context.user_data.pop(TARGET_KEY, None)
    if not isinstance(chat_id, int) or not text or not text.strip():
        await update.message.reply_text("❌ الرسالة فارغة أو غير صالحة.")
        return True
    row = _get_group(get_db, chat_id)
    if not row or int(row["owner_user_id"]) != update.effective_user.id:
        await update.message.reply_text("❌ لم تعد هذه المجموعة مرتبطة بحسابك.")
        return True
    try:
        if not await _is_group_admin(context, chat_id, update.effective_user.id):
            _delete_group(get_db, chat_id, update.effective_user.id)
            await update.message.reply_text("⚠️ لم تعد تملك صلاحية إدارة المجموعة، وتم إلغاء الربط.")
            return True
        if not await _bot_can_publish(context, chat_id):
            await update.message.reply_text("❌ AliBot لا يستطيع إرسال الرسائل إلى هذه المجموعة.")
            return True
        await context.bot.send_message(chat_id=chat_id, text=text.strip()[:4000])
        await update.message.reply_text("✅ تم نشر الرسالة في المجموعة بنجاح.")
    except Exception as exc:
        logger.warning("Group publish failed: %s", type(exc).__name__)
        await update.message.reply_text("❌ تعذر نشر الرسالة. تحقق من وجود AliBot وصلاحياته.")
    return True


async def handle_my_chat_member(update, context, get_db):
    change = update.my_chat_member
    if not change or not update.effective_chat or not change.from_user:
        return
    chat = update.effective_chat
    if chat.type not in {"group", "supergroup"}:
        return
    new_status = change.new_chat_member.status
    old_status = change.old_chat_member.status
    if new_status in {"member", "administrator"} and old_status in {"left", "kicked"}:
        try:
            if not await _is_group_admin(context, chat.id, change.from_user.id):
                logger.info("Group add ignored: inviter is not an admin")
                return
            if not await _bot_can_publish(context, chat.id):
                logger.info("Group add ignored: bot cannot publish")
                return
            _upsert_group(get_db, chat.id, chat.title or "مجموعة", change.from_user)
            await context.bot.send_message(chat_id=chat.id, text="✅ تم ربط AliBot بهذه المجموعة.\nاستخدم /groups في الخاص لإدارة النشر.")
        except Exception as exc:
            logger.warning("Group registration failed: %s", type(exc).__name__)


async def admin_group_publisher_callback(update: Update, context, get_db, admin_id: int):
    query = update.callback_query
    await query.answer()
    if not query.from_user or query.from_user.id != admin_id or not _is_private(update):
        return
    rows = _list_groups(get_db)
    if not rows:
        text = "👥 <b>إدارة مجموعات AliBot</b>\n\nلا توجد مجموعات مرتبطة حاليًا."
    else:
        lines = [f"👥 <b>المجموعات المرتبطة: {len(rows)}</b>\n"]
        for row in rows[:50]:
            lines.append(f"• {html.escape(str(row['title'] or 'مجموعة'))} — <code>{row['chat_id']}</code>")
        if len(rows) > 50:
            lines.append(f"\n... و{len(rows)-50} أخرى")
        text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data=ADMIN_CALLBACK)],
                                     [InlineKeyboardButton("🔙 Smart Operations", callback_data="admin_smart_operations")]])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


def register_group_publisher(app: Any, get_db, admin_id: int | None = None) -> None:
    ensure_schema(get_db)
    marker = "_alibot_group_publisher_registered"
    if getattr(app, marker, False):
        return
    setattr(app, marker, True)
    app.add_handler(CommandHandler("groups", lambda u, c: groups_command(u, c, get_db)))
    app.add_handler(ChatMemberHandler(lambda u, c: handle_my_chat_member(u, c, get_db), ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(lambda u, c: group_publisher_callback(u, c, get_db),
                                          pattern=rf"^({CALLBACK}|{CONSENT_PREFIX}(yes|no)|{PUBLISH_PREFIX}-?\d+|{REMOVE_PREFIX}-?\d+)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                                   lambda u, c: process_group_publisher_message(u, c, get_db), group=-1))
    if admin_id is not None:
        app.add_handler(CallbackQueryHandler(lambda u, c: admin_group_publisher_callback(u, c, get_db, admin_id),
                                              pattern=rf"^{ADMIN_CALLBACK}$"))
