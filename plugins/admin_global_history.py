"""Owner-only global download-history reset.

This module clears download-history rows for every user in the database,
including users outside the current admin pagination window. User records are
preserved. After the reset, the bot sends a fresh start/welcome message to
all stored Telegram chat IDs that can still receive messages.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

_RESET = "admin_global_history_reset"
_CONFIRM = "admin_global_history_confirm"
_CANCEL = "admin_global_history_cancel"


_START_MESSAGE = (
    "🎬 <b>مرحبًا بك من جديد في AliBot</b> 🤍\n\n"
    "📥 بوت سريع وسهل لتحميل الفيديوهات والصوتيات من مختلف المنصات.\n\n"
    "⚡ أرسل رابط الفيديو أو الصوت، واختر الجودة المناسبة.\n"
    "🆓 تحميل مجاني • 🚀 سريع • 🎧 جودة عالية\n\n"
    "👇 أرسل الرابط الآن وابدأ التحميل!"
)


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _audit(get_db, admin_id: int, action: str, details: str = "") -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO admin_audit_logs "
            "(admin_id, action, target_id, details, created_at) VALUES (?, ?, NULL, ?, ?)",
            (admin_id, action, details, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()


def global_history_button() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("🧹 مسح سجل الجميع", callback_data=_RESET)]


def register_admin_global_history(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: global_reset_prompt(u, c, get_db, owner_id),
            pattern=r"^admin_global_history_reset$",
        ),
        group=-1,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: global_reset_confirm(u, c, get_db, owner_id),
            pattern=r"^admin_global_history_confirm$",
        ),
        group=-1,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: global_reset_cancel(u, c, get_db, owner_id),
            pattern=r"^admin_global_history_cancel$",
        ),
        group=-1,
    )


async def global_reset_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return

    conn = get_db()
    try:
        user_count = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        history_count = int(conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0])
    finally:
        conn.close()

    await query.edit_message_text(
        "⚠️ <b>مسح سجل جميع المستخدمين</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 المستخدمون في قاعدة البيانات: <b>{user_count}</b>\n"
        f"🧾 عمليات التحميل التي ستُمسح: <b>{history_count}</b>\n\n"
        "سيتم مسح <b>كل</b> سجلات التحميل، بما فيها سجلات المستخدمين غير الظاهرين في الصفحة الحالية، "
        "وتصفير عدادات التحميل.\n\n"
        "👤 لن يتم حذف حسابات المستخدمين أو بياناتهم الأساسية.\n"
        "📨 بعد التنفيذ سيحاول البوت إرسال رسالة بدء جديدة لكل مستخدم محفوظ في قاعدة البيانات.\n\n"
        "⚠️ العملية لا يمكن التراجع عنها.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ نعم، امسح سجل الجميع", callback_data=_CONFIRM)],
            [InlineKeyboardButton("❌ إلغاء", callback_data=_CANCEL)],
        ]),
    )


async def global_reset_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer("تم الإلغاء")
    if not _authorized(update, owner_id):
        return
    await query.edit_message_text(
        "❌ <b>تم إلغاء العملية</b>\n\nلم يتم حذف أي سجل أو تعديل أي مستخدم.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
        ]),
    )


async def _send_start_messages(bot, user_ids: list[int]) -> tuple[int, int]:
    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=_START_MESSAGE,
                parse_mode="HTML",
            )
            sent += 1
        except Exception:
            failed += 1
        # Stay comfortably below Telegram's general broadcast rate.
        await asyncio.sleep(0.05)
    return sent, failed


async def global_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return

    conn = get_db()
    try:
        rows = conn.execute("SELECT user_id FROM users ORDER BY user_id ASC").fetchall()
        user_ids = [int(row["user_id"]) for row in rows]
        conn.execute("BEGIN IMMEDIATE")
        deleted = int(conn.execute("DELETE FROM downloads").rowcount)
        conn.execute("UPDATE users SET downloads = 0")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    _audit(
        get_db,
        owner_id,
        "clear_all_download_history",
        details=f"deleted_download_rows={deleted};user_count={len(user_ids)}",
    )

    sent, failed = await _send_start_messages(context.bot, user_ids)

    await query.edit_message_text(
        "✅ <b>تم تنفيذ المسح الشامل بنجاح</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🗑️ سجلات التحميل المحذوفة: <b>{deleted}</b>\n"
        f"👥 المستخدمون المشمولون: <b>{len(user_ids)}</b>\n"
        "📊 عدادات التحميل: <b>0</b>\n"
        f"📨 رسائل البدء المرسلة: <b>{sent}</b>\n"
        f"⚠️ تعذر الإرسال إلى: <b>{failed}</b>\n\n"
        "👤 حسابات المستخدمين وبياناتهم الأساسية بقيت محفوظة في قاعدة البيانات.\n"
        "ℹ️ فشل الإرسال يعني عادةً أن المستخدم حظر البوت أو أن Telegram لا يسمح بإرسال الرسالة إليه؛ لا يتم حذف سجله لهذا السبب.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
            [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
        ]),
    )
