"""Safe discussion-thread publisher for AliBot.

This MVP only captures Telegram's automatic channel->discussion forwards and
allows an owner-only one-shot test reply. It does not auto-publish.
"""

from __future__ import annotations

import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from .admin_common import authorize
from .admin_control_center import audit


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS discussion_links (
    channel_id INTEGER PRIMARY KEY,
    channel_title TEXT NOT NULL DEFAULT '',
    channel_username TEXT,
    discussion_chat_id INTEGER NOT NULL UNIQUE,
    discussion_title TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
)
"""

THREAD_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS discussion_threads (
    discussion_chat_id INTEGER NOT NULL,
    discussion_message_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    channel_post_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (discussion_chat_id, discussion_message_id)
)
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_discussion_publisher(get_db) -> None:
    conn = get_db()
    conn.execute(TABLE_SQL)
    conn.execute(THREAD_TABLE_SQL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_threads_channel_post "
        "ON discussion_threads(channel_id, channel_post_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_threads_captured "
        "ON discussion_threads(captured_at DESC)"
    )
    conn.commit()
    conn.close()


def _capture_automatic_forward(get_db, message) -> bool:
    if not getattr(message, "is_automatic_forward", False):
        return False

    origin = getattr(message, "forward_origin", None)
    if origin is None or getattr(origin, "type", None) != "channel":
        return False

    channel = getattr(origin, "chat", None)
    chat = getattr(message, "chat", None)
    channel_id = getattr(channel, "id", None)
    discussion_chat_id = getattr(chat, "id", None)
    channel_post_id = getattr(origin, "message_id", None)
    discussion_message_id = getattr(message, "message_id", None)

    if None in (channel_id, discussion_chat_id, channel_post_id, discussion_message_id):
        return False

    now = _now()
    channel_title = getattr(channel, "title", None) or ""
    channel_username = getattr(channel, "username", None)
    discussion_title = getattr(chat, "title", None) or ""

    conn = get_db()
    conn.execute(
        """
        INSERT INTO discussion_links (
            channel_id, channel_title, channel_username,
            discussion_chat_id, discussion_title, enabled,
            first_seen, last_seen
        )
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            channel_title=excluded.channel_title,
            channel_username=excluded.channel_username,
            discussion_chat_id=excluded.discussion_chat_id,
            discussion_title=excluded.discussion_title,
            last_seen=excluded.last_seen
        """,
        (
            int(channel_id), str(channel_title), channel_username,
            int(discussion_chat_id), str(discussion_title), now, now,
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO discussion_threads (
            discussion_chat_id, discussion_message_id,
            channel_id, channel_post_id, captured_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(discussion_chat_id), int(discussion_message_id),
            int(channel_id), int(channel_post_id), now,
        ),
    )
    conn.commit()
    conn.close()
    return True


async def capture_discussion_forward(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db) -> None:
    message = update.effective_message
    if message is None:
        return
    try:
        _capture_automatic_forward(get_db, message)
    except Exception:
        # Never allow an observability/capture feature to affect message handling.
        return


def _publisher_text(get_db) -> str:
    conn = get_db()
    links = conn.execute(
        """
        SELECT channel_id, channel_title, channel_username,
               discussion_chat_id, discussion_title, last_seen
        FROM discussion_links
        WHERE enabled=1
        ORDER BY last_seen DESC
        LIMIT 20
        """
    ).fetchall()
    conn.close()

    lines = [
        "📢 <b>نشر مناقشات Telegram — بيئة الاختبار</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🛡️ الوضع الحالي: <b>اختبار يدوي فقط</b>",
        "لا يوجد نشر تلقائي في هذه المرحلة.",
        "",
    ]

    if not links:
        lines += [
            "⏳ لم يتم التقاط أي Discussion Thread بعد.",
            "",
            "أضف AliBot إلى مجموعة مناقشة قناة الاختبار، ثم انشر",
            "منشورًا جديدًا في القناة. سيقوم النظام بالتقاط الـThread تلقائيًا.",
        ]
    else:
        lines.append("📡 المناقشات المكتشفة:")
        for row in links:
            name = row["channel_title"] or row["channel_username"] or str(row["channel_id"])
            lines.append(
                f"• {html.escape(str(name))} → "
                f"<code>{int(row['discussion_chat_id'])}</code>"
            )

    return "\n".join(lines)


def _publisher_keyboard(get_db) -> InlineKeyboardMarkup:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT l.channel_id, l.channel_title, l.channel_username,
               l.discussion_chat_id, t.discussion_message_id, t.channel_post_id
        FROM discussion_links l
        LEFT JOIN discussion_threads t
          ON t.discussion_chat_id = l.discussion_chat_id
         AND t.captured_at = (
             SELECT MAX(t2.captured_at)
             FROM discussion_threads t2
             WHERE t2.discussion_chat_id = l.discussion_chat_id
         )
        WHERE l.enabled=1
        ORDER BY l.last_seen DESC
        LIMIT 20
        """
    ).fetchall()
    conn.close()

    buttons = []
    for row in rows:
        name = str(row["channel_title"] or row["channel_username"] or row["channel_id"])[:28]
        if row["discussion_message_id"] is not None:
            buttons.append([
                InlineKeyboardButton(
                    f"🧪 اختبار: {name}",
                    callback_data=(
                        f"admin_discussion_test_{int(row['discussion_chat_id'])}_"
                        f"{int(row['discussion_message_id'])}"
                    ),
                )
            ])

    buttons.append([
        InlineKeyboardButton("🔄 تحديث", callback_data="admin_discussion_publisher")
    ])
    buttons.append([
        InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_home")
    ])
    return InlineKeyboardMarkup(buttons)


async def admin_discussion_publisher_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int
) -> None:
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    audit(get_db, int(update.effective_user.id), "open_discussion_publisher")
    await query.edit_message_text(
        _publisher_text(get_db),
        parse_mode="HTML",
        reply_markup=_publisher_keyboard(get_db),
    )


async def admin_discussion_test_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int
) -> None:
    query = update.callback_query
    if not authorize(update, get_db, owner_id, "center.view"):
        await query.answer()
        return

    data = query.data or ""
    parts = data.split("_")
    if len(parts) != 5:
        await query.answer("طلب غير صالح", show_alert=True)
        return

    try:
        discussion_chat_id = int(parts[3])
        discussion_message_id = int(parts[4])
    except ValueError:
        await query.answer("بيانات الاختبار غير صالحة", show_alert=True)
        return

    conn = get_db()
    row = conn.execute(
        """
        SELECT l.channel_id, l.discussion_chat_id, t.discussion_message_id
        FROM discussion_links l
        JOIN discussion_threads t
          ON t.discussion_chat_id = l.discussion_chat_id
        WHERE l.discussion_chat_id = ?
          AND t.discussion_message_id = ?
          AND l.enabled = 1
        LIMIT 1
        """,
        (discussion_chat_id, discussion_message_id),
    ).fetchone()
    conn.close()

    if not row:
        await query.answer("الـThread غير مسجل أو لم يعد صالحًا.", show_alert=True)
        return

    await query.answer("جارِ إرسال رسالة الاختبار...")
    try:
        sent = await context.bot.send_message(
            chat_id=discussion_chat_id,
            text="🧪 <b>AliBot Discussion Test</b>\n\nتم إرسال هذه الرسالة من بيئة الاختبار.",
            parse_mode="HTML",
            reply_parameters=ReplyParameters(
                message_id=discussion_message_id,
                allow_sending_without_reply=False,
            ),
        )
        audit(
            get_db,
            int(update.effective_user.id),
            "discussion_test_publish",
            discussion_chat_id,
            f"thread_message_id={discussion_message_id};sent_message_id={sent.message_id}",
        )
        await query.edit_message_text(
            "✅ <b>نجح اختبار النشر</b>\n\n"
            f"💬 Discussion: <code>{discussion_chat_id}</code>\n"
            f"🧵 Thread message: <code>{discussion_message_id}</code>\n"
            f"📨 Sent message: <code>{sent.message_id}</code>\n\n"
            "تحقق الآن من ظهور الرسالة داخل تعليقات المنشور الصحيح.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 نشر المناقشات", callback_data="admin_discussion_publisher")],
                [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_home")],
            ]),
        )
    except Exception as exc:
        audit(
            get_db,
            int(update.effective_user.id),
            "discussion_test_publish_failed",
            discussion_chat_id,
            type(exc).__name__,
        )
        await query.edit_message_text(
            "❌ <b>فشل اختبار النشر</b>\n\n"
            "تحقق من أن AliBot عضو في مجموعة المناقشة ولديه صلاحية إرسال الرسائل، "
            "ثم أعد الاختبار.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 نشر المناقشات", callback_data="admin_discussion_publisher")],
                [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_home")],
            ]),
        )


def register_discussion_publisher(app, get_db, owner_id: int) -> None:
    init_discussion_publisher(get_db)

    app.add_handler(
        MessageHandler(
            filters.ALL,
            lambda update, context: capture_discussion_forward(update, context, get_db),
        ),
        group=-185,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: admin_discussion_publisher_callback(
                update, context, get_db, owner_id
            ),
            pattern=r"^admin_discussion_publisher$",
        ),
        group=-90,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: admin_discussion_test_callback(
                update, context, get_db, owner_id
            ),
            pattern=r"^admin_discussion_test_-?\d+_-?\d+$",
        ),
        group=-90,
    )
