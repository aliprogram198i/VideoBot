"""Group Control Center for AliBot.

Tracks the bot's membership/administrator state per group and exposes a
read-only permission view plus a safe Telegram add flow. Telegram remains the
authority: the dashboard never fabricates or escalates rights.
"""

from __future__ import annotations

import html
import re
from datetime import datetime

from telegram import ChatAdministratorRights, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ChatMemberHandler, ContextTypes

from .admin_common import authorize
from .admin_control_center import audit


REQUIRED_RIGHTS = {
    "can_manage_chat": "إدارة المجموعة",
    "can_delete_messages": "حذف الرسائل",
    "can_restrict_members": "حظر/تقييد الأعضاء",
    "can_invite_users": "دعوة الأعضاء",
    "can_pin_messages": "تثبيت الرسائل",
    "can_manage_topics": "إدارة المواضيع",
    "can_change_info": "تغيير معلومات المجموعة",
    "can_promote_members": "إدارة المشرفين",
}

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS managed_groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    username TEXT,
    chat_type TEXT NOT NULL DEFAULT '',
    bot_status TEXT NOT NULL DEFAULT 'unknown',
    is_admin INTEGER NOT NULL DEFAULT 0,
    can_manage_chat INTEGER NOT NULL DEFAULT 0,
    can_delete_messages INTEGER NOT NULL DEFAULT 0,
    can_restrict_members INTEGER NOT NULL DEFAULT 0,
    can_invite_users INTEGER NOT NULL DEFAULT 0,
    can_pin_messages INTEGER NOT NULL DEFAULT 0,
    can_manage_topics INTEGER NOT NULL DEFAULT 0,
    can_change_info INTEGER NOT NULL DEFAULT 0,
    can_promote_members INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    left_at TEXT
)
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_group_control(get_db) -> None:
    conn = get_db()
    conn.execute(TABLE_SQL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_managed_groups_status "
        "ON managed_groups(bot_status, is_admin)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_managed_groups_seen "
        "ON managed_groups(last_seen DESC)"
    )
    conn.commit()
    conn.close()


def _rights(member) -> dict[str, int]:
    return {name: int(bool(getattr(member, name, False))) for name in REQUIRED_RIGHTS}


def _upsert_group(get_db, chat, member) -> None:
    now = _now()
    status = str(getattr(member, "status", "unknown"))
    is_admin = int(status in {"administrator", "creator"})
    rights = _rights(member)
    username = getattr(chat, "username", None)
    title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or ""

    conn = get_db()
    conn.execute(
        """
        INSERT INTO managed_groups (
            chat_id, title, username, chat_type, bot_status, is_admin,
            can_manage_chat, can_delete_messages, can_restrict_members,
            can_invite_users, can_pin_messages, can_manage_topics,
            can_change_info, can_promote_members, first_seen, last_seen, left_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            title=excluded.title,
            username=excluded.username,
            chat_type=excluded.chat_type,
            bot_status=excluded.bot_status,
            is_admin=excluded.is_admin,
            can_manage_chat=excluded.can_manage_chat,
            can_delete_messages=excluded.can_delete_messages,
            can_restrict_members=excluded.can_restrict_members,
            can_invite_users=excluded.can_invite_users,
            can_pin_messages=excluded.can_pin_messages,
            can_manage_topics=excluded.can_manage_topics,
            can_change_info=excluded.can_change_info,
            can_promote_members=excluded.can_promote_members,
            last_seen=excluded.last_seen,
            left_at=excluded.left_at
        """,
        (
            int(chat.id), str(title), username, str(getattr(chat, "type", "")),
            status, is_admin, rights["can_manage_chat"], rights["can_delete_messages"],
            rights["can_restrict_members"], rights["can_invite_users"],
            rights["can_pin_messages"], rights["can_manage_topics"],
            rights["can_change_info"], rights["can_promote_members"],
            now, now, now if status in {"left", "kicked"} else None,
        ),
    )
    conn.commit()
    conn.close()


async def group_membership_update(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db) -> None:
    change = update.my_chat_member
    if not change or not change.chat or not change.new_chat_member:
        return
    chat = change.chat
    if getattr(chat, "type", None) not in {"group", "supergroup"}:
        return
    _upsert_group(get_db, chat, change.new_chat_member)


def _status_label(status: str, is_admin: int) -> str:
    if status in {"left", "kicked"}:
        return "🔴 غير موجود"
    if is_admin:
        return "🟢 مشرف"
    if status == "member":
        return "🟡 عضو"
    return "⚪ غير معروف"


def _groups_text(get_db) -> str:
    conn = get_db()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN is_admin=1 THEN 1 ELSE 0 END) AS admins,
               SUM(CASE WHEN bot_status IN ('left','kicked') THEN 1 ELSE 0 END) AS absent
        FROM managed_groups
        """
    ).fetchone()
    conn.close()
    return (
        "👥 <b>مركز إدارة المجموعات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📚 المجموعات المسجلة: <b>{int(row['total'] or 0)}</b>\n"
        f"🛡️ البوت مشرف فيها: <b>{int(row['admins'] or 0)}</b>\n"
        f"🔴 لم يعد البوت موجودًا: <b>{int(row['absent'] or 0)}</b>\n\n"
        "يعتمد المركز على حالة Telegram الفعلية ولا يمنح نفسه أي صلاحية."
    )


def _groups_keyboard(get_db) -> InlineKeyboardMarkup:
    conn = get_db()
    rows = conn.execute(
        "SELECT chat_id, title, bot_status, is_admin FROM managed_groups "
        "ORDER BY is_admin DESC, last_seen DESC LIMIT 25"
    ).fetchall()
    conn.close()

    buttons = []
    for row in rows:
        title = str(row["title"] or f"Chat {row['chat_id']}")[:32]
        state = "🟢" if row["is_admin"] else ("🔴" if row["bot_status"] in {"left", "kicked"} else "🟡")
        buttons.append([InlineKeyboardButton(
            f"{state} {title}", callback_data=f"admin_group_view_{int(row['chat_id'])}"
        )])

    buttons += [
        [InlineKeyboardButton("➕ إضافة AliBot إلى مجموعة", url="https://t.me/MyVideoDownloaderAliBot?startgroup=true")],
        [InlineKeyboardButton("🛡️ صلاحيات الإضافة الافتراضية", callback_data="admin_group_default_rights")],
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_groups")],
        [InlineKeyboardButton("🎛️ لوحة القيادة", callback_data="admin_home")],
    ]
    return InlineKeyboardMarkup(buttons)


async def admin_groups_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    audit(get_db, int(update.effective_user.id), "open_group_control_center")
    await query.edit_message_text(_groups_text(get_db), parse_mode="HTML", reply_markup=_groups_keyboard(get_db))


async def admin_group_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    match = re.fullmatch(r"admin_group_view_(-?\d+)", query.data or "")
    if not match:
        return
    chat_id = int(match.group(1))
    conn = get_db()
    row = conn.execute("SELECT * FROM managed_groups WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    if not row:
        await query.edit_message_text(
            "❌ لم تعد المجموعة مسجلة في مركز الإدارة.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 المجموعات", callback_data="admin_groups")]])
        )
        return

    lines = [
        "👥 <b>تفاصيل المجموعة</b>", "━━━━━━━━━━━━━━━━━━━━",
        f"🏷️ الاسم: <b>{html.escape(str(row['title'] or 'غير معروف'))}</b>",
        f"🆔 Chat ID: <code>{chat_id}</code>",
        f"📌 النوع: <code>{html.escape(str(row['chat_type']))}</code>",
        f"🤖 حالة البوت: {_status_label(row['bot_status'], row['is_admin'])}",
        "", "🛡️ <b>صلاحيات Telegram الحالية</b>",
    ]
    for key, label in REQUIRED_RIGHTS.items():
        lines.append(f"{'✅' if row[key] else '❌'} {label}")
    lines += [
        "", f"🕒 آخر مزامنة: <code>{html.escape(str(row['last_seen']))}</code>",
        "ℹ️ الصلاحيات المعروضة هي ما أعادته Telegram، وليست إعدادات افتراضية من لوحة الإدارة.",
    ]
    audit(get_db, int(update.effective_user.id), "view_group_permissions", chat_id)
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 مزامنة من Telegram", callback_data=f"admin_group_sync_{chat_id}")],
            [InlineKeyboardButton("🔙 المجموعات", callback_data="admin_groups")],
        ])
    )


async def admin_group_sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer("جارِ المزامنة...")
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    match = re.fullmatch(r"admin_group_sync_(-?\d+)", query.data or "")
    if not match:
        return
    chat_id = int(match.group(1))
    try:
        member = await context.bot.get_chat_member(chat_id, context.bot.id)
        chat = await context.bot.get_chat(chat_id)
        _upsert_group(get_db, chat, member)
        audit(get_db, int(update.effective_user.id), "sync_group_permissions", chat_id)
        await query.edit_message_text(
            "✅ تمت مزامنة حالة البوت والصلاحيات من Telegram.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 فتح المجموعة", callback_data=f"admin_group_view_{chat_id}")],
                [InlineKeyboardButton("🔙 المجموعات", callback_data="admin_groups")],
            ])
        )
    except Exception as exc:
        audit(get_db, int(update.effective_user.id), "sync_group_failed", chat_id, type(exc).__name__)
        await query.edit_message_text(
            "⚠️ تعذر مزامنة المجموعة من Telegram. غالبًا لم يعد البوت عضوًا فيها أو لا يمكن الوصول إليها.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 المجموعات", callback_data="admin_groups")]])
        )


async def admin_group_default_rights_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return

    rights = ChatAdministratorRights(
        can_manage_chat=True, can_delete_messages=True, can_restrict_members=True,
        can_invite_users=True, can_pin_messages=True, can_manage_topics=True,
        can_change_info=True, can_promote_members=False,
    )
    try:
        await context.bot.set_my_default_administrator_rights(rights=rights, for_channels=False)
        audit(get_db, int(update.effective_user.id), "set_default_group_admin_rights")
        text = (
            "✅ تم ضبط صلاحيات الإضافة الافتراضية المقترحة.\n\n"
            "عند إضافة AliBot كمشرف، سيقترح Telegram هذه الصلاحيات، "
            "ويمكن للمستخدم تعديلها قبل التأكيد.\n\n"
            "⚠️ هذا لا يمنح البوت صلاحيات في أي مجموعة موجودة حاليًا."
        )
    except Exception as exc:
        audit(get_db, int(update.effective_user.id), "set_default_group_admin_rights_failed", details=type(exc).__name__)
        text = "❌ تعذر تحديث الصلاحيات الافتراضية المقترحة من Telegram."

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة AliBot إلى مجموعة", url="https://t.me/MyVideoDownloaderAliBot?startgroup=true")],
            [InlineKeyboardButton("🔙 المجموعات", callback_data="admin_groups")],
        ])
    )


def register_group_control(app, get_db, owner_id: int) -> None:
    init_group_control(get_db)
    app.add_handler(
        ChatMemberHandler(
            lambda update, context: group_membership_update(update, context, get_db),
            ChatMemberHandler.MY_CHAT_MEMBER,
        ),
        group=-190,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: admin_groups_callback(update, context, get_db, owner_id),
            pattern=r"^admin_groups$",
        ), group=-90
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: admin_group_view_callback(update, context, get_db, owner_id),
            pattern=r"^admin_group_view_-?\d+$",
        ), group=-90
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: admin_group_sync_callback(update, context, get_db, owner_id),
            pattern=r"^admin_group_sync_-?\d+$",
        ), group=-90
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: admin_group_default_rights_callback(update, context, get_db, owner_id),
            pattern=r"^admin_group_default_rights$",
        ), group=-90
    )
