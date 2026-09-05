"""AliBot Smart Operations dashboard.

Read-only diagnostics: it never modifies or deletes existing user/download data.
"""

import os
import shutil
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes


CALLBACK = "admin_smart_operations"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_prefix():
    return datetime.now().strftime("%Y-%m-%d")


def _safe_count(conn, sql, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return None


def _table_names(conn):
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {str(row[0]) for row in rows}
    except Exception:
        return set()


def _column_names(conn, table):
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row[1]) for row in rows}
    except Exception:
        return set()


def collect_smart_operations(get_db):
    """Collect read-only operational metrics from the existing DB."""
    conn = get_db()
    try:
        tables = _table_names(conn)
        users = _safe_count(conn, "SELECT COUNT(*) FROM users") if "users" in tables else 0
        downloads = _safe_count(
            conn,
            "SELECT COUNT(*) FROM downloads WHERE created_at >= ?",
            (_today_prefix(),),
        ) if "downloads" in tables else 0
        active = _safe_count(
            conn,
            "SELECT COUNT(*) FROM users WHERE last_seen >= ?",
            (_today_prefix(),),
        ) if "users" in tables else 0

        website = None
        website_count = 0
        if "downloads" in tables:
            rows = conn.execute(
                """SELECT website, COUNT(*) AS count
                   FROM downloads
                   WHERE created_at >= ?
                   GROUP BY website
                   ORDER BY count DESC
                   LIMIT 1""",
                (_today_prefix(),),
            ).fetchall()
            if rows:
                website = rows[0][0] or "غير معروف"
                website_count = int(rows[0][1] or 0)

        total_today = downloads or 0
        platform_pct = round((website_count / total_today) * 100, 1) if total_today else 0

        # Only calculate success rate when a real status/result column exists.
        success_rate = None
        if "downloads" in tables:
            cols = _column_names(conn, "downloads")
            status_col = next((c for c in ("status", "result", "download_status") if c in cols), None)
            if status_col:
                total = _safe_count(conn, f"SELECT COUNT(*) FROM downloads WHERE created_at >= ?", (_today_prefix(),))
                success = _safe_count(
                    conn,
                    f"SELECT COUNT(*) FROM downloads WHERE created_at >= ? AND LOWER(CAST({status_col} AS TEXT)) IN ('success','successful','ok','completed','done','1')",
                    (_today_prefix(),),
                )
                if total:
                    success_rate = round((success or 0) * 100 / total, 1)

        # Discover an existing error table without creating one or changing the DB.
        error_summary = None
        error_count = 0
        error_table = next(
            (name for name in tables if name.lower() in {"errors", "error_logs", "ai_errors", "bot_errors"}),
            None,
        )
        if error_table:
            cols = _column_names(conn, error_table)
            message_col = next((c for c in ("error", "message", "error_message", "details") if c in cols), None)
            time_col = next((c for c in ("created_at", "timestamp", "occurred_at") if c in cols), None)
            if message_col:
                where = f"WHERE {time_col} >= ?" if time_col else ""
                params = (_today_prefix(),) if time_col else ()
                row = conn.execute(
                    f"SELECT {message_col}, COUNT(*) AS count FROM {error_table} {where} GROUP BY {message_col} ORDER BY count DESC LIMIT 1",
                    params,
                ).fetchone()
                error_count = _safe_count(conn, f"SELECT COUNT(*) FROM {error_table} {where}", params) or 0
                if row:
                    error_summary = str(row[0])[:120]

        return {
            "users": users or 0,
            "downloads_today": total_today,
            "active_today": active or 0,
            "website": website,
            "platform_pct": platform_pct,
            "success_rate": success_rate,
            "error_summary": error_summary,
            "error_count": error_count,
            "ai_configured": bool(os.getenv("GEMINI_API_KEY")),
        }
    finally:
        conn.close()


def _keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data=CALLBACK)],
        [InlineKeyboardButton("📊 لوحة الإحصائيات", callback_data="admin_dashboard_30")],
        [InlineKeyboardButton("🤖 الذكاء الاصطناعي", callback_data="admin_ai")],
        [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_home")],
    ])


def render_smart_operations(data):
    status = "🟢 مستقر"
    ai_status = "🟢 تعمل" if data["ai_configured"] else "🟠 غير مهيأة"
    success = f"{data['success_rate']}%" if data["success_rate"] is not None else "غير متاح — لا يوجد حقل نتيجة موثوق"

    text = (
        "🤖 <b>Smart Operations</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{status}\n"
        f"🧠 مراقبة الأخطاء     {ai_status}\n"
        f"📥 تحميلات اليوم      {data['downloads_today']}\n"
        f"✅ معدل النجاح        {success}\n"
        f"👥 مستخدمون نشطون     {data['active_today']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🚨 <b>المشاكل الحالية</b>\n"
    )

    if data["error_count"]:
        text += f"🟠 أخطاء مسجلة اليوم: {data['error_count']}\n"
    else:
        text += "🟢 لا توجد مشاكل حرجة مسجلة\n"

    text += "\n━━━━━━━━━━━━━━━━━━\n\n"
    text += "🌐 <b>المنصة الأكثر استخدامًا</b>\n"
    if data["website"]:
        text += f"{data['website']} — {data['platform_pct']}%\n"
    else:
        text += "لا توجد بيانات اليوم\n"

    text += "\n⚠️ <b>أكثر خطأ متكرر</b>\n"
    text += (data["error_summary"] + "\n") if data["error_summary"] else "لا توجد بيانات أخطاء مسجلة\n"

    text += "\n🧠 <b>تحليل ذكي</b>\n"
    if data["error_summary"]:
        text += "النظام يرصد الخطأ المسجل ويعرضه للتحليل والمتابعة.\n"
    elif data["ai_configured"]:
        text += "لا توجد مشكلة مسجلة حاليًا تحتاج إلى تحليل.\n"
    else:
        text += "التحليل الذكي غير مهيأ حاليًا.\n"

    text += f"\n🕒 آخر فحص: <code>{_now()}</code>"
    return text


async def smart_operations_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, admin_id):
    query = update.callback_query
    await query.answer()
    if not update.effective_user or update.effective_user.id != admin_id:
        return
    try:
        data = collect_smart_operations(get_db)
        await query.edit_message_text(render_smart_operations(data), parse_mode="HTML", reply_markup=_keyboard())
    except Exception:
        await query.edit_message_text(
            "🤖 <b>Smart Operations</b>\n━━━━━━━━━━━━━━━━━━\n\n🔴 تعذر قراءة بيانات المراقبة حاليًا.\n\nℹ️ لم يتم تعديل قاعدة البيانات.",
            parse_mode="HTML",
            reply_markup=_keyboard(),
        )


def register_smart_operations(app, get_db, admin_id):
    """Register the isolated Smart Operations callback."""
    app.add_handler(
        CallbackQueryHandler(
            lambda update, context: smart_operations_callback(update, context, get_db, admin_id),
            pattern=r"^" + CALLBACK + r"$",
        )
    )
