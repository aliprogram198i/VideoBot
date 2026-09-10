"""Phase-3 backup/recovery admin surface.

Read-only by default: this module exposes backup inventory and DB integrity
information without mutating the production database or storage. Actual backup
creation/restore can be wired later behind explicit permissions and audit gates.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from .admin_authorization import authorize


def _db_path() -> Path:
    return Path(os.getenv("BOT_DB_PATH", "/app/data/bot_stats.db")).resolve()


def _read_db() -> dict[str, Any]:
    path = _db_path()
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            journal = db.execute("PRAGMA journal_mode").fetchone()[0]
        return {"exists": True, "path": str(path), "size": path.stat().st_size, "integrity": integrity, "journal": journal}
    except Exception as exc:
        return {"exists": True, "path": str(path), "error": type(exc).__name__}


async def _callback(update, context, admin_id: int) -> None:
    query = update.callback_query
    if not authorize(update, admin_id):
        await query.answer()
        return
    info = _read_db()
    if not info.get("exists"):
        text = "🗄️ <b>النسخ والاستعادة</b>\n\n⚠️ قاعدة البيانات غير موجودة في المسار المتوقع."
    elif info.get("error"):
        text = "🗄️ <b>النسخ والاستعادة</b>\n\n⚠️ تعذر فحص قاعدة البيانات بأمان."
    else:
        size_mb = info["size"] / (1024 * 1024)
        text = (
            "🗄️ <b>النسخ والاستعادة</b>\n\n"
            f"📁 المسار: <code>{info['path']}</code>\n"
            f"💾 الحجم: <b>{size_mb:.2f} MB</b>\n"
            f"🧪 Integrity: <b>{info['integrity']}</b>\n"
            f"📝 Journal: <b>{info['journal']}</b>\n\n"
            "🔒 الإنشاء والاستعادة غير مفعّلان من هذه الواجهة حالياً، لمنع أي عملية تخريبية غير مقصودة."
        )
    await query.answer()
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ مركز الأمان", callback_data="admin_security")],
        [InlineKeyboardButton("🧾 التدقيق", callback_data="admin_audit")],
        [InlineKeyboardButton("↩️ مركز العمليات", callback_data="admin_ops_dashboard")],
    ]))
    raise ApplicationHandlerStop


def register_admin_backup_recovery(app: Any, admin_id: int) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: _callback(u, c, admin_id), pattern=r"^admin_backup_recovery$"), group=-150)
