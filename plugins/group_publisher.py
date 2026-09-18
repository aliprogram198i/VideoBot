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
ADMIN_SELECTED_KEY = "admin_group_selected"
ADMIN_MESSAGE_KEY = "admin_group_message"
WAITING_KEY = "group_publisher_waiting_message"
TARGET_KEY = "group_publisher_target"
ADD_GROUP_URL = "https://t.me/MyVideoDownloaderAliBot?startgroup=publisher"


def ensure_schema(get_db) -> None:
    conn = get_db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_groups (
            chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            owner_user_id INTEGER NOT NULL, owner_username TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active',
            last_publish_at TEXT, publish_count INTEGER NOT NULL DEFAULT 0)""")
        cols={str(r[1]) for r in conn.execute("PRAGMA table_info(bot_groups)").fetchall()}
        for n,ddl in (("enabled","ALTER TABLE bot_groups ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"),("status","ALTER TABLE bot_groups ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"),("last_publish_at","ALTER TABLE bot_groups ADD COLUMN last_publish_at TEXT"),("publish_count","ALTER TABLE bot_groups ADD COLUMN publish_count INTEGER NOT NULL DEFAULT 0")):
            if n not in cols: conn.execute(ddl)
        conn.execute("CREATE TABLE IF NOT EXISTS group_publish_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL,actor_user_id INTEGER NOT NULL,actor_type TEXT NOT NULL,status TEXT NOT NULL,message_preview TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,error_type TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_group_publish_logs_chat_created ON group_publish_logs(chat_id,created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_group_publish_logs_status_created ON group_publish_logs(status,created_at)")
        conn.commit()
    finally:
        conn.close()


def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")


async def _user_can_publish(context, chat_id: int, user_id: int) -> bool:
    member = await context.bot.get_chat_member(chat_id, user_id)
    if member.status in {"creator", "administrator", "member"}:
        return bool(getattr(member, "can_send_messages", True))
    if member.status == "restricted":
        return bool(getattr(member, "can_send_messages", False))
    return False


async def _bot_can_publish(context, chat_id: int) -> bool:
    me = await context.bot.get_me()
    member = await context.bot.get_chat_member(chat_id, me.id)
    if member.status in {"creator", "administrator", "member"}:
        return bool(getattr(member, "can_send_messages", True))
    if member.status == "restricted":
        return bool(getattr(member, "can_send_messages", False))
    return False


def _list_groups(get_db, owner_user_id: int | None = None):
    conn = get_db()
    try:
        if owner_user_id is None:
            return conn.execute("SELECT chat_id,title,owner_user_id,owner_username,created_at,updated_at,enabled,status,last_publish_at,publish_count FROM bot_groups ORDER BY title COLLATE NOCASE,chat_id").fetchall()
        return conn.execute("SELECT chat_id,title,owner_username,enabled,status,last_publish_at,publish_count FROM bot_groups WHERE owner_user_id=? ORDER BY title COLLATE NOCASE,chat_id", (owner_user_id,)).fetchall()
    finally:
        conn.close()


def _get_group(get_db, chat_id: int):
    conn = get_db()
    try:
        return conn.execute(
            """SELECT chat_id,title,owner_user_id,owner_username,created_at,updated_at,
                      enabled,status,last_publish_at,publish_count
               FROM bot_groups WHERE chat_id=?""",
            (chat_id,),
        ).fetchone()
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


def _set_group_enabled(get_db,chat_id,enabled):
    conn=get_db()
    try: conn.execute("UPDATE bot_groups SET enabled=?,updated_at=? WHERE chat_id=?",(1 if enabled else 0,datetime.now().isoformat(),chat_id)); conn.commit()
    finally: conn.close()

def _record_publish(get_db,chat_id,actor_user_id,actor_type,message,status,error_type=None):
    conn=get_db()
    try:
        now=datetime.now().isoformat()
        conn.execute(
            "INSERT INTO group_publish_logs(chat_id,actor_user_id,actor_type,status,message_preview,created_at,error_type) "
            "VALUES(?,?,?,?,?,?,?)",
            (chat_id,actor_user_id,actor_type,status,message[:160],now,error_type),
        )
        if status == "success":
            conn.execute(
                "UPDATE bot_groups SET last_publish_at=?,publish_count=publish_count+1,status='active',updated_at=? "
                "WHERE chat_id=?",
                (now,now,chat_id),
            )
        elif status == "failed":
            conn.execute(
                "UPDATE bot_groups SET status='publish_error',updated_at=? WHERE chat_id=?",
                (now, chat_id),
            )
        conn.commit()
    finally: conn.close()


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
        "بعد الإضافة، يكفي أن تكون لديك صلاحية إرسال الرسائل في المجموعة؛ لا يشترط أن تكون مديرًا.\n\n"
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
            if not row or int(row["owner_user_id"]) != query.from_user.id or not int(row["enabled"]):
                await query.edit_message_text("❌ هذه المجموعة غير مرتبطة بحسابك.")
                return
            if not await _user_can_publish(context, chat_id, query.from_user.id):
                conn = get_db()
                try:
                    conn.execute("UPDATE bot_groups SET status='owner_access_lost',updated_at=? WHERE chat_id=? AND owner_user_id=?",
                                 (datetime.now().isoformat(), chat_id, query.from_user.id))
                    conn.commit()
                finally:
                    conn.close()
                await query.edit_message_text("⚠️ لم تعد تملك صلاحية إرسال الرسائل في هذه المجموعة حاليًا. تم الاحتفاظ بالربط ويمكنك المحاولة مجددًا بعد عودة الصلاحية.")
                return
            if not await _bot_can_publish(context, chat_id):
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE bot_groups SET status='bot_permission_error',updated_at=? WHERE chat_id=?",
                        (datetime.now().isoformat(), chat_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
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


async def process_admin_group_message(update, context, get_db, admin_id: int) -> bool:
    if not update.effective_user or update.effective_user.id != admin_id or not update.message or not _is_private(update):
        return False
    if not context.user_data.get("admin_group_waiting_message"):
        return False
    message = (update.message.text or "").strip()
    if not message:
        await update.message.reply_text("❌ الرسالة فارغة.")
        return True
    selected = set(context.user_data.get(ADMIN_SELECTED_KEY, set()))
    if not selected:
        await update.message.reply_text("❌ لم يتم اختيار أي مجموعة.")
        return True
    context.user_data["admin_group_waiting_message"] = False
    context.user_data[ADMIN_MESSAGE_KEY] = message[:4000]
    rows = {int(r["chat_id"]): str(r["title"] or "مجموعة") for r in _list_groups(get_db)}
    names = [html.escape(rows.get(cid, str(cid))) for cid in selected]
    await update.message.reply_text(
        "🔎 <b>تأكيد النشر</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        + "👥 المجموعات: " + str(len(selected)) + "\n"
        + "\n".join("• " + n for n in names[:50])
        + "\n\n📝 <b>المعاينة:</b>\n" + html.escape(message[:1000]),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد وإرسال", callback_data="admin_group_send_confirm")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="admin_group_send_cancel")],
        ])
    )
    return True

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
        if not await _user_can_publish(context, chat_id, update.effective_user.id):
            conn = get_db()
            try:
                conn.execute("UPDATE bot_groups SET status='owner_access_lost',updated_at=? WHERE chat_id=? AND owner_user_id=?",
                             (datetime.now().isoformat(), chat_id, update.effective_user.id))
                conn.commit()
            finally:
                conn.close()
            await update.message.reply_text("⚠️ لا تملك حاليًا صلاحية إرسال الرسائل في هذه المجموعة. تم الاحتفاظ بالربط.")
            return True
        if not await _bot_can_publish(context, chat_id):
            await update.message.reply_text("❌ AliBot لا يستطيع إرسال الرسائل إلى هذه المجموعة.")
            return True
        await context.bot.send_message(chat_id=chat_id, text=text.strip()[:4000])
        _record_publish(get_db,chat_id,update.effective_user.id,"owner",text.strip(),"success")
        await update.message.reply_text("✅ تم نشر الرسالة في المجموعة بنجاح.")
    except Exception as exc:
        _record_publish(get_db,chat_id,update.effective_user.id,"owner",text.strip(),"failed",type(exc).__name__)
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

    # Telegram sends MY_CHAT_MEMBER whenever the bot's own membership changes.
    # Keep the linkage instead of deleting it when permissions temporarily change.
    if new_status in {"left", "kicked"}:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE bot_groups SET status=?,enabled=0,updated_at=? WHERE chat_id=?",
                ("bot_left" if new_status == "left" else "bot_kicked", datetime.now().isoformat(), chat.id),
            )
            conn.commit()
        finally:
            conn.close()
        return

    if new_status == "restricted":
        can_send = bool(getattr(change.new_chat_member, "can_send_messages", False))
        conn = get_db()
        try:
            conn.execute(
                "UPDATE bot_groups SET status=?,updated_at=? WHERE chat_id=?",
                ("active" if can_send else "bot_permission_error", datetime.now().isoformat(), chat.id),
            )
            conn.commit()
        finally:
            conn.close()
        return

    if new_status in {"member", "administrator"}:
        try:
            bot_can_send = await _bot_can_publish(context, chat.id)
            if old_status in {"left", "kicked"}:
                if not await _user_can_publish(context, chat.id, change.from_user.id):
                    logger.info("Group add ignored: inviter cannot send messages")
                    return
                _upsert_group(get_db, chat.id, chat.title or "مجموعة", change.from_user)
                # Respect a previous manual disablement on re-add.
                row = _get_group(get_db, chat.id)
                if row and int(row["enabled"]) and bot_can_send:
                    status = "active"
                elif row and not int(row["enabled"]):
                    status = "disabled"
                else:
                    status = "bot_permission_error"
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE bot_groups SET status=?,updated_at=? WHERE chat_id=?",
                        (status, datetime.now().isoformat(), chat.id),
                    )
                    conn.commit()
                finally:
                    conn.close()
                if bot_can_send:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text="✅ تم ربط AliBot بهذه المجموعة.\nاستخدم /groups في الخاص لإدارة النشر.",
                    )
            else:
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE bot_groups SET status=?,updated_at=? WHERE chat_id=?",
                        ("active" if bot_can_send else "bot_permission_error", datetime.now().isoformat(), chat.id),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            logger.warning("Group membership update failed: %s", type(exc).__name__)


async def admin_group_publisher_callback(update: Update, context, get_db, admin_id: int):
    q=update.callback_query; await q.answer()
    if not q.from_user or q.from_user.id!=admin_id or not _is_private(update): return
    ensure_schema(get_db); data=q.data or ""
    if data=="admin_group_logs":
        conn=get_db()
        try: rows=conn.execute("SELECT chat_id,actor_type,message_preview,status,created_at FROM group_publish_logs ORDER BY id DESC LIMIT 30").fetchall()
        finally: conn.close()
        lines=["📜 <b>سجل عمليات النشر</b>","━━━━━━━━━━━━━━━━━━"]
        for r in rows: lines.append(f"{'✅' if r['status']=='success' else '❌'} {r['created_at'][:19]} | {r['chat_id']} | {r['actor_type']} | {html.escape(r['message_preview'][:70])}")
        await q.edit_message_text("\n".join(lines),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المجموعات",callback_data=ADMIN_CALLBACK)]])); return
    if data.startswith("admin_group_toggle_"):
        cid=int(data[len("admin_group_toggle_"):]); row=_get_group(get_db,cid)
        if row: _set_group_enabled(get_db,cid,not bool(row["enabled"]))
        await q.edit_message_text("✅ تم تغيير حالة النشر.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المجموعات",callback_data=ADMIN_CALLBACK)]])); return

    if data == "admin_group_send":
        rows = _list_groups(get_db)
        if not rows:
            await q.edit_message_text("📢 <b>إرسال إلى المجموعات</b>\n\nلا توجد مجموعات مرتبطة.", parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المجموعات", callback_data=ADMIN_CALLBACK)]]))
            return
        context.user_data[ADMIN_SELECTED_KEY] = set()
        context.user_data.pop(ADMIN_MESSAGE_KEY, None)
        buttons = [[InlineKeyboardButton(("☐ " + str(r["title"] or "مجموعة"))[:34], callback_data="admin_group_select_" + str(int(r["chat_id"])))] for r in rows[:50]]
        buttons.append([InlineKeyboardButton("✍️ متابعة وكتابة الرسالة", callback_data="admin_group_send_compose")])
        buttons.append([InlineKeyboardButton("🔙 إدارة المجموعات", callback_data=ADMIN_CALLBACK)])
        await q.edit_message_text("📢 <b>إرسال رسالة للمجموعات</b>\n\nاختر مجموعة واحدة أو عدة مجموعات.", parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("admin_group_select_"):
        cid = int(data[len("admin_group_select_"):])
        selected = set(context.user_data.get(ADMIN_SELECTED_KEY, set()))
        if cid in selected:
            selected.remove(cid)
        else:
            selected.add(cid)
        context.user_data[ADMIN_SELECTED_KEY] = selected
        rows = _list_groups(get_db)
        buttons = [[InlineKeyboardButton(("☑️ " if int(r["chat_id"]) in selected else "☐ ") + str(r["title"] or "مجموعة"), callback_data="admin_group_select_" + str(int(r["chat_id"])))] for r in rows[:50]]
        buttons.append([InlineKeyboardButton("✍️ كتابة الرسالة (" + str(len(selected)) + " مختارة)", callback_data="admin_group_send_compose")])
        buttons.append([InlineKeyboardButton("🔙 إدارة المجموعات", callback_data=ADMIN_CALLBACK)])
        await q.edit_message_text("📢 <b>اختيار المجموعات</b>\n\nيمكن اختيار مجموعة واحدة أو عدة مجموعات.", parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "admin_group_send_compose":
        selected = set(context.user_data.get(ADMIN_SELECTED_KEY, set()))
        if not selected:
            await q.answer("اختر مجموعة واحدة على الأقل.", show_alert=True)
            return
        context.user_data["admin_group_waiting_message"] = True
        await q.edit_message_text("✍️ <b>اكتب رسالة النشر الآن</b>\n\nبعد إرسالها ستظهر لك معاينة وزر تأكيد قبل أي إرسال.", parse_mode="HTML")
        return

    if data == "admin_group_send_cancel":
        context.user_data.pop(ADMIN_SELECTED_KEY, None)
        context.user_data.pop(ADMIN_MESSAGE_KEY, None)
        context.user_data.pop("admin_group_waiting_message", None)
        await q.edit_message_text("❌ تم إلغاء عملية النشر.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المجموعات", callback_data=ADMIN_CALLBACK)]]))
        return

    if data == "admin_group_send_confirm":
        message = context.user_data.get(ADMIN_MESSAGE_KEY)
        selected = sorted({int(x) for x in context.user_data.get(ADMIN_SELECTED_KEY, set())})
        if not message or not selected:
            await q.edit_message_text(
                "❌ انتهت عملية النشر أو لم يتم تحديد مجموعات.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 إدارة المجموعات", callback_data=ADMIN_CALLBACK)]]
                ),
            )
            return

        # Consume the confirmation state before any network I/O. A repeated
        # Telegram callback cannot send the same broadcast twice.
        context.user_data.pop("admin_group_waiting_message", None)
        context.user_data.pop(ADMIN_SELECTED_KEY, None)
        context.user_data.pop(ADMIN_MESSAGE_KEY, None)

        results = []
        for cid in selected[:50]:
            try:
                row = _get_group(get_db, cid)
                if not row:
                    results.append("❌ " + str(cid) + ": غير مرتبطة")
                    continue
                if not int(row["enabled"]):
                    results.append("⏸️ " + html.escape(str(row["title"] or cid)) + ": معطلة")
                    continue
                if not await _bot_can_publish(context, cid):
                    conn = get_db()
                    try:
                        conn.execute(
                            "UPDATE bot_groups SET status='bot_permission_error',updated_at=? WHERE chat_id=?",
                            (datetime.now().isoformat(), cid),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    _record_publish(
                        get_db, cid, admin_id, "admin", message, "failed", "BotPermissionError"
                    )
                    results.append("❌ " + html.escape(str(row["title"] or cid)) + ": صلاحية AliBot غير متاحة")
                    continue

                await context.bot.send_message(chat_id=cid, text=message[:4000])
                _record_publish(get_db, cid, admin_id, "admin", message, "success")
                results.append("✅ " + html.escape(str(row["title"] or cid)))
            except Exception as exc:
                _record_publish(
                    get_db, cid, admin_id, "admin", message, "failed", type(exc).__name__
                )
                results.append("❌ " + str(cid) + ": " + type(exc).__name__)

        await q.edit_message_text(
            "📢 <b>نتيجة النشر</b>\n\n" + "\n".join(results[:50]),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 إدارة المجموعات", callback_data=ADMIN_CALLBACK)]]
            ),
        )
        return

    if data.startswith("admin_group_remove_"):
        cid=int(data[len("admin_group_remove_"):]); _delete_group(get_db,cid)
        await q.edit_message_text("✅ تمت إزالة المجموعة.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المجموعات",callback_data=ADMIN_CALLBACK)]])); return
    rows=_list_groups(get_db)
    active_count=sum(1 for r in rows if int(r["enabled"]) and r["status"]=="active")
    disabled_count=sum(1 for r in rows if not int(r["enabled"]))
    issue_count=len(rows)-active_count-disabled_count
    lines=[
        f"👥 <b>إدارة المجموعات — {len(rows)}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🟢 نشطة: {active_count}   ⏸️ معطلة: {disabled_count}   ⚠️ تحتاج فحص: {issue_count}",
        "",
    ]
    for r in rows[:30]:
        status=str(r["status"] or "active")
        state="🟢" if int(r["enabled"]) and status=="active" else ("⏸️" if not int(r["enabled"]) else "⚠️")
        lines.append(
            f"{state} <b>{html.escape(str(r['title'] or 'مجموعة'))}</b> | "
            f"👤 {html.escape(str(r['owner_username'] or r['owner_user_id']))} | "
            f"📢 {r['publish_count']} | 🕒 {str(r['last_publish_at'] or 'لم ينشر بعد')[:19]} | "
            f"<code>{html.escape(status)}</code>"
        )
    buttons=[[InlineKeyboardButton("🔄 تحديث",callback_data=ADMIN_CALLBACK)]]
    for r in rows[:20]:
        cid=int(r["chat_id"])
        buttons.append([InlineKeyboardButton(("⛔ تعطيل " if int(r["enabled"]) else "✅ تفعيل ")+str(r["title"] or "مجموعة")[:18],callback_data=f"admin_group_toggle_{cid}"),InlineKeyboardButton("🗑️ إزالة",callback_data=f"admin_group_remove_{cid}")])
    buttons.append([InlineKeyboardButton("📢 إرسال رسالة للمجموعات",callback_data="admin_group_send")])
    buttons.append([InlineKeyboardButton("📜 سجل النشر",callback_data="admin_group_logs")])
    buttons.append([InlineKeyboardButton("🔙 Smart Operations",callback_data="admin_smart_operations")])
    await q.edit_message_text("\n".join(lines) if rows else "👥 <b>إدارة المجموعات</b>\n\nلا توجد مجموعات مرتبطة.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(buttons))


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
    if admin_id is not None:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                                       lambda u, c: process_admin_group_message(u, c, get_db, admin_id)), group=-3)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                                   lambda u, c: process_group_publisher_message(u, c, get_db)), group=-1)
    if admin_id is not None:
        app.add_handler(CallbackQueryHandler(lambda u, c: admin_group_publisher_callback(u, c, get_db, admin_id),
                                              pattern=rf"^(?:{ADMIN_CALLBACK}|admin_group_toggle_-?\d+|admin_group_remove_-?\d+|admin_group_logs|admin_group_send|admin_group_send_compose|admin_group_send_cancel|admin_group_send_confirm|admin_group_select_-?\d+)$"))
