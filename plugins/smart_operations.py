"""AliBot Smart Operations dashboard."""

import html
import os
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from .smart_download_control import register_smart_download_control
from .user_experience_v2 import register_user_experience_v2
from .telegram_message_guard import install as install_telegram_message_guard
from .link_investigator import install as install_link_investigator
from .group_publisher import register_group_publisher
from .resolver_monitor import render_resolver_monitor

CALLBACK = "admin_smart_operations"


def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def _today_prefix(): return datetime.now().strftime("%Y-%m-%d")

def _safe_count(conn, sql, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return None

def _table_names(conn):
    try:
        return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except Exception:
        return set()

def _column_names(conn, table):
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()

def collect_smart_operations(get_db):
    conn = get_db()
    try:
        tables = _table_names(conn)
        users = _safe_count(conn, "SELECT COUNT(*) FROM users") if "users" in tables else 0
        downloads = _safe_count(conn, "SELECT COUNT(*) FROM downloads WHERE created_at >= ?", (_today_prefix(),)) if "downloads" in tables else 0
        active = _safe_count(conn, "SELECT COUNT(*) FROM users WHERE last_seen >= ?", (_today_prefix(),)) if "users" in tables else 0
        website = None; website_count = 0
        if "downloads" in tables:
            rows = conn.execute("SELECT website,COUNT(*) FROM downloads WHERE created_at>=? GROUP BY website ORDER BY COUNT(*) DESC LIMIT 1", (_today_prefix(),)).fetchall()
            if rows: website, website_count = rows[0][0] or "غير معروف", int(rows[0][1] or 0)
        total = downloads or 0
        success_rate = None
        if "downloads" in tables:
            cols = _column_names(conn, "downloads")
            status_col = next((c for c in ("status","result","download_status") if c in cols), None)
            if status_col:
                total2 = _safe_count(conn, "SELECT COUNT(*) FROM downloads WHERE created_at>=?", (_today_prefix(),))
                success = _safe_count(conn, f"SELECT COUNT(*) FROM downloads WHERE created_at>=? AND LOWER(CAST({status_col} AS TEXT)) IN ('success','successful','ok','completed','done','1')", (_today_prefix(),))
                if total2: success_rate = round((success or 0)*100/total2, 1)
        error_summary=None; error_count=0; error_monitoring=False
        error_table=next((n for n in tables if n.lower() in {"errors","error_logs","ai_errors","bot_errors"}),None)
        if error_table:
            cols=_column_names(conn,error_table); message_col=next((c for c in ("error","message","error_message","details") if c in cols),None)
            time_col=next((c for c in ("created_at","timestamp","occurred_at") if c in cols),None)
            if message_col:
                error_monitoring=True; where=f"WHERE {time_col}>=?" if time_col else ""; params=(_today_prefix(),) if time_col else ()
                row=conn.execute(f"SELECT {message_col},COUNT(*) FROM {error_table} {where} GROUP BY {message_col} ORDER BY COUNT(*) DESC LIMIT 1",params).fetchone()
                error_count=_safe_count(conn,f"SELECT COUNT(*) FROM {error_table} {where}",params) or 0
                if row: error_summary=str(row[0])[:120]
        return {"users":users or 0,"downloads_today":total,"active_today":active or 0,"website":website,
                "platform_pct":round((website_count/total)*100,1) if total else 0,"success_rate":success_rate,
                "error_summary":error_summary,"error_count":error_count,"error_monitoring":error_monitoring,
                "ai_configured":bool(os.getenv("GEMINI_API_KEY"))}
    finally: conn.close()

def _keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 إدارة المجموعات", callback_data="admin_group_publisher")],
        [InlineKeyboardButton("🔄 تحديث", callback_data=CALLBACK)],
        [InlineKeyboardButton("🧠 Fallback Intelligence", callback_data="admin_fallback_intelligence")],
        [InlineKeyboardButton("📈 Smart Analytics", callback_data="admin_smart_analytics")],
        [InlineKeyboardButton("📊 لوحة الإحصائيات", callback_data="admin_dashboard_30")],
        [InlineKeyboardButton("🤖 الذكاء الاصطناعي", callback_data="admin_ai")],
        [InlineKeyboardButton("🔙 مركز التحكم الإداري", callback_data="admin_home")],
    ])

def render_smart_operations(data):
    success=f"{data['success_rate']}%" if data["success_rate"] is not None else "غير متاح"
    text=("🤖 <b>Smart Operations</b>\n━━━━━━━━━━━━━━━━━━\n\n"
          f"🟢 قراءة البيانات مستقرة\n📥 تحميلات اليوم: {data['downloads_today']}\n"
          f"✅ معدل النجاح: {success}\n👥 مستخدمون نشطون: {data['active_today']}\n\n"
          "🚨 <b>المشاكل الحالية</b>\n")
    text += f"🟠 أخطاء مسجلة اليوم: {data['error_count']}\n" if data["error_count"] else "🟢 لا توجد مشاكل حرجة مسجلة\n"
    text += "\n🌐 <b>المنصة الأكثر استخدامًا</b>\n"
    text += f"{html.escape(str(data['website']))} — {data['platform_pct']}%\n" if data["website"] else "لا توجد بيانات اليوم\n"
    text += f"\n⚠️ <b>أكثر خطأ متكرر</b>\n{html.escape(str(data['error_summary'])) if data['error_summary'] else 'لا توجد بيانات أخطاء مسجلة'}\n"
    text += f"\n🕒 آخر فحص: <code>{_now()}</code>"
    return text

async def smart_operations_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, admin_id: int):
    query=update.callback_query; await query.answer()
    if not update.effective_user or update.effective_user.id != admin_id: return
    try:
        data = collect_smart_operations(get_db)
        text = render_smart_operations(data)
        text += "\n\n" + render_resolver_monitor(get_db, days=1)
        await query.edit_message_text(text,parse_mode="HTML",reply_markup=_keyboard())
    except Exception:
        await query.edit_message_text("🤖 <b>Smart Operations</b>\n\n🔴 تعذر قراءة البيانات.\nℹ️ لم يتم تعديل قاعدة البيانات.",parse_mode="HTML",reply_markup=_keyboard())

def _has_smart_operations_handler(app):
    for handlers in getattr(app,"handlers",{}).values():
        for handler in handlers:
            if isinstance(handler,CallbackQueryHandler):
                pattern=getattr(handler,"pattern",None); p=getattr(pattern,"pattern",None) or (pattern if isinstance(pattern,str) else "")
                if p==rf"^{CALLBACK}$": return True
    return False

def register_smart_operations(app,get_db,admin_id):
    install_telegram_message_guard(); register_smart_download_control(app); install_link_investigator(); register_user_experience_v2(app)
    register_group_publisher(app,get_db,admin_id)
    if _has_smart_operations_handler(app): return
    app.add_handler(CallbackQueryHandler(lambda u,c: smart_operations_callback(u,c,get_db,admin_id),pattern=rf"^{CALLBACK}$"))
