"""Isolated administrative temporary-storage cleanup."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


async def storage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    await query.edit_message_text(
        "🧹 <b>تفريغ ذاكرة التخزين</b>\n\nسيتم حذف مجلدات العمليات المؤقتة التي أنشأها البوت فقط.\n\n🔒 قاعدة البيانات وملفات البوت لن تتأثر.\n\nهل تريد المتابعة؟",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم، نظّف الآن", callback_data="admin_storage_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_storage_cancel"),
        ]]),
    )


async def storage_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    await query.edit_message_text("⏳ جاري تنظيف ذاكرة التخزين...\n\n🧹 يرجى الانتظار.")
    tmp_dir = Path("/tmp")
    deleted_files = deleted_dirs = freed_bytes = 0
    try:
        if tmp_dir.exists():
            for item in tmp_dir.iterdir():
                if not item.is_dir() or not item.name.startswith("videobot_"):
                    continue
                try:
                    for path in item.rglob("*"):
                        try:
                            if path.is_file():
                                freed_bytes += path.stat().st_size
                                deleted_files += 1
                        except OSError:
                            pass
                    shutil.rmtree(item, ignore_errors=True)
                    deleted_dirs += 1
                except Exception:
                    continue
        freed_mb = freed_bytes / 1024 / 1024
        size_text = f"{freed_bytes / 1024 / 1024 / 1024:.2f} GB" if freed_bytes >= 1024**3 else f"{freed_mb:.2f} MB"
        await query.edit_message_text(
            f"✅ <b>تم تنظيف ذاكرة التخزين</b>\n\n🗑️ الملفات المحذوفة: {deleted_files}\n📁 المجلدات: {deleted_dirs}\n💾 المساحة المحررة: {size_text}\n\n🔒 قاعدة البيانات وملفات البوت لم تتأثر.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 تنظيف مرة أخرى", callback_data="admin_storage")],
                [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
            ]),
        )
    except Exception as exc:
        await query.edit_message_text(
            f"❌ حدث خطأ أثناء تنظيف التخزين.\n\nنوع الخطأ: {type(exc).__name__}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")]]),
        )


async def storage_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> None:
    query = update.callback_query
    await query.answer("تم إلغاء العملية.")
    if not _authorized(update, owner_id):
        return
    await query.edit_message_text("🎛️ <b>مركز التحكم الإداري</b>\n\nاختر القسم المطلوب:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")]
    ]))


def register_admin_storage(app: Any, owner_id: int) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: storage_callback(u, c, owner_id), pattern=r"^admin_storage$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: storage_confirm_callback(u, c, owner_id), pattern=r"^admin_storage_confirm$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: storage_cancel_callback(u, c, owner_id), pattern=r"^admin_storage_cancel$"), group=-1)
