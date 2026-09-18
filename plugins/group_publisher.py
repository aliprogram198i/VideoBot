"""AliBot group publisher layer.

Allows an authorized group administrator to add AliBot to a group and,
from the private bot chat, publish explicitly approved text messages to
registered groups. It never broadcasts automatically and never accepts
publication requests from non-admin users.
"""

from __future__ import annotations

import html
import logging
import sqlite3
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

CALLBACK = "group_publisher"
PUBLISH_PREFIX = "group_publish_"
REMOVE_PREFIX = "group_remove_"
WAITING_KEY = "group_publisher_waiting_message"
TARGET_KEY = "group_publisher_target"

ADD_GROUP_URL = "https://t.me/MyVideoDownloaderAliBot?startgroup=publisher"


def ensure_schema(get_db) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                owner_user_id INTEGER NOT NULL,
                owner_username TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")


async def _is_group_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    member = await context.bot.get_chat_member(chat_id, user_id)
    return member.status in {"creator", "administrator"}


async def _bot_can_publish(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    me = await context.bot.get_me()
    member = await context.bot.get_chat_member(chat_id, me.id)
    return member.status in {"creator", "administrator", "member"}


def _list_groups(get_db, owner_user_id: int):
    conn = get_db()
    try:
        return conn.execute(
            """
            SELECT chat_id, title, owner_username
            FROM bot_groups
            WHERE owner_user_id = ?
            ORDER BY title COLLATE NOCASE, chat_id
            """,
            (owner_user_id,),
        ).fetchall()
    finally:
        conn.close()


def _get_group(get_db, chat_id: int):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT chat_id, title, owner_user_id, owner_username FROM bot_groups WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    finally:
        conn.close()


def _upsert_group(get_db, chat_id: int, title: str, owner) -> None:
    from datetime import datetime

    now = datetime.now().isoformat()
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO bot_groups
                (chat_id, title, owner_user_id, owner_username, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                updated_at = excluded.updated_at
            """,
            (chat_id, title[:255], owner.id, owner.username, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _delete_group(get_db, chat_id: int, owner_user_id: int) -> bool:
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM bot_groups WHERE chat_id = ? AND owner_user_id = ?",
            (chat_id, owner_user_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ إضافة AliBot إلى مجموعة", url=ADD_GROUP_URL)],
            [InlineKeyboardButton("📢 إدارة المجموعات", callback_data=CALLBACK)],
        ]
    )


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


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db) -> None:
    if not _is_private(update) or not update.effective_user:
        return
    ensure_schema(get_db)
    rows = _list_groups(get_db, update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text(
            "👥 <b>إدارة مجموعات AliBot</b>\n\n"
            "أضف AliBot إلى مجموعة تملكها أو تديرها، ثم سيظهر هنا ويمكنك "
            "إرسال رسائل إليها من محادثتك الخاصة مع البوت.",
            parse_mode="HTML",
            reply_markup=_main_keyboard(),
        )
        return
    await update.effective_message.reply_text(
        "👥 <b>مجموعاتك المرتبطة</b>\n\n"
        "اختر مجموعة للنشر أو احذف الربط.",
        parse_mode="HTML",
        reply_markup=_groups_keyboard(rows),
    )


async def group_publisher_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    get_db,
) -> None:
    query = update.callback_query
    await query.answer()
    if not query.from_user or not _is_private(update):
        return
    ensure_schema(get_db)

    if query.data == CALLBACK:
        rows = _list_groups(get_db, query.from_user.id)
        await query.edit_message_text(
            "👥 <b>مجموعاتك المرتبطة</b>\n\n"
            "لا توجد مجموعات مرتبطة بعد."
            if not rows
            else "👥 <b>مجموعاتك المرتبطة</b>\n\nاختر مجموعة للنشر أو احذف الربط.",
            parse_mode="HTML",
            reply_markup=_groups_keyboard(rows) if rows else _main_keyboard(),
        )
        return

    try:
        if query.data.startswith(PUBLISH_PREFIX):
            chat_id = int(query.data[len(PUBLISH_PREFIX):])
            row = _get_group(get_db, chat_id)
            if not row or int(row["owner_user_id"]) != query.from_user.id:
                await query.edit_message_text("❌ هذه المجموعة غير مرتبطة بحسابك.")
                return
            if not await _is_group_admin(context, chat_id, query.from_user.id):
                _delete_group(get_db, chat_id, query.from_user.id)
                await query.edit_message_text(
                    "⚠️ لم تعد تملك صلاحية إدارة هذه المجموعة، لذلك أُلغي الربط تلقائيًا."
                )
                return
            if not await _bot_can_publish(context, chat_id):
                await query.edit_message_text(
                    "❌ لا يستطيع AliBot إرسال الرسائل إلى هذه المجموعة حاليًا."
                )
                return
            context.user_data[WAITING_KEY] = True
            context.user_data[TARGET_KEY] = chat_id
            await query.edit_message_text(
                "📢 <b>إرسال رسالة إلى المجموعة</b>\n\n"
                f"👥 {html.escape(str(row['title'] or 'المجموعة'))}\n\n"
                "أرسل نص الرسالة الآن.\n"
                "❌ للإلغاء: /cancel",
                parse_mode="HTML",
            )
            return

        if query.data.startswith(REMOVE_PREFIX):
            chat_id = int(query.data[len(REMOVE_PREFIX):])
            if _delete_group(get_db, chat_id, query.from_user.id):
                await query.edit_message_text("✅ تم إلغاء ربط المجموعة.")
            else:
                await query.edit_message_text("❌ تعذر إلغاء الربط أو أن المجموعة ليست مرتبطة بك.")
    except (TypeError, ValueError):
        await query.edit_message_text("❌ طلب غير صالح.")


async def process_group_publisher_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    get_db,
) -> bool:
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
        await update.message.reply_text(
            "❌ تعذر نشر الرسالة. تأكد أن AliBot ما زال موجودًا في المجموعة ويستطيع إرسال الرسائل."
        )
    return True


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db) -> None:
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
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "✅ تم ربط AliBot بهذه المجموعة.\n\n"
                    "يمكن لمدير المجموعة الآن استخدام /groups في المحادثة الخاصة مع AliBot "
                    "لإدارة النشر."
                ),
            )
        except Exception as exc:
            logger.warning("Group registration failed: %s", type(exc).__name__)


def register_group_publisher(app: Any, get_db) -> None:
    ensure_schema(get_db)
    marker = "_alibot_group_publisher_registered"
    if getattr(app, marker, False):
        return
    setattr(app, marker, True)

    app.add_handler(CommandHandler(
        "groups",
        lambda update, context: groups_command(update, context, get_db),
    ))
    app.add_handler(ChatMemberHandler(
        lambda update, context: handle_my_chat_member(update, context, get_db),
        ChatMemberHandler.MY_CHAT_MEMBER,
    ))
    app.add_handler(CallbackQueryHandler(
        lambda update, context: group_publisher_callback(update, context, get_db),
        pattern=rf"^({CALLBACK}|{PUBLISH_PREFIX}-?\\d+|{REMOVE_PREFIX}-?\\d+)$",
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        lambda update, context: process_group_publisher_message(update, context, get_db),
    ))
