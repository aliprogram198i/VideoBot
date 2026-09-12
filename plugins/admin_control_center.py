import json
import shutil
import subprocess
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes

from .admin_common import authorize
from .admin_user_history import register_admin_user_history
from .admin_global_history import register_admin_global_history
from .admin_ai import register_admin_ai
from .admin_stats import register_admin_stats
from .admin_broadcast import register_admin_broadcast
from .admin_storage import register_admin_storage
from .admin_users import register_admin_users
from .smart_operations import register_smart_operations


def _now():
    return datetime.now().isoformat(timespec="seconds")


def admin_keyboard():
    """Single source of truth for the top-level admin navigation."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 مركز العمليات", callback_data="admin_ops_dashboard"),
         InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
        [InlineKeyboardButton("📥 التنزيلات والبيانات", callback_data="admin_records"),
         InlineKeyboardButton("🤖 العمليات الذكية", callback_data="admin_smart_operations")],
        [InlineKeyboardButton("📡 المراقبة الذكية", callback_data="admin_observability"),
         InlineKeyboardButton("📢 الإذاعة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🩺 صحة النظام", callback_data="admin_health"),
         InlineKeyboardButton("🧠 الذكاء الاصطناعي", callback_data="admin_ai")],
        [InlineKeyboardButton("🧹 التخزين", callback_data="admin_storage"),
         InlineKeyboardButton("🧾 سجل التدقيق", callback_data="admin_audit")],
        [InlineKeyboardButton("🛡️ الأدوار والصلاحيات", callback_data="admin_roles")],
    ])


def _records_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 سجل الروابط والتحميلات", callback_data="admin_download_log_0")],
        [InlineKeyboardButton("🧹 مسح سجل الجميع", callback_data="admin_global_history_reset")],
        [InlineKeyboardButton("👥 إدارة سجلات مستخدم", callback_data="admin_users_page_0")],
        [InlineKeyboardButton("📊 مركز العمليات", callback_data="admin_ops_dashboard")],
    ])


def register_admin_control_center(app, get_db, owner_id):
    init_admin_control_center(get_db, owner_id)
    register_admin_stats(app, get_db, owner_id)
    register_admin_broadcast(app, get_db, owner_id)
    register_admin_storage(app, owner_id)
    register_admin_users(app, get_db, owner_id)
    register_admin_user_history(app, get_db, owner_id)
    register_admin_global_history(app, get_db, owner_id)
    register_admin_ai(app, get_db, owner_id)
    register_smart_operations(app, get_db, owner_id)

    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_control_center_callback(u, c, get_db, owner_id),
        pattern=r"^admin_home$",
    ), group=-100)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_control_center_callback(u, c, get_db, owner_id),
        pattern=r"^admin_control_center$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_records_callback(u, c, get_db, owner_id),
        pattern=r"^admin_records$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_health_callback(u, c, get_db, owner_id),
        pattern=r"^admin_health$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_audit_callback(u, c, get_db, owner_id),
        pattern=r"^admin_audit$",
    ), group=-1)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_roles_callback(u, c, get_db, owner_id),
        pattern=r"^admin_roles$",
    ), group=-1)


def init_admin_control_center(get_db, owner_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_roles (
            user_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL,
            permissions TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_created_at "
        "ON admin_audit_logs(created_at DESC)"
    )
    cur.execute(
        """INSERT INTO admin_roles (user_id, role, permissions, created_at, updated_at)
        VALUES (?, 'owner', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET role='owner', updated_at=excluded.updated_at""",
        (owner_id, json.dumps({'*': True}), _now(), _now()),
    )
    conn.commit()
    conn.close()


def audit(get_db, admin_id, action, target_id=None, details=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO admin_audit_logs "
        "(admin_id, action, target_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (admin_id, action, target_id, details, _now()),
    )
    conn.commit()
    conn.close()


def _authorized(update, get_db, owner_id, permission="center.view"):
    return authorize(update, get_db, owner_id, permission)


def _home_text(get_db=None):
    if get_db is None:
        return (
            "🎛️ <b>لوحة القيادة الإدارية</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🟢 النظام الإداري يعمل\n"
            "🔐 الوصول محمي\n"
            "🧾 التدقيق مفعّل\n\n"
            "اختر القسم المطلوب:"
        )
    conn = None
    try:
        conn = get_db()
        users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        downloads = int(conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0])
        banned = int(conn.execute("SELECT COALESCE(SUM(is_banned),0) FROM users").fetchone()[0])
        today = _now()[:10]
        today_downloads = int(conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE substr(created_at,1,10)=?", (today,)
        ).fetchone()[0])
        today_users = int(conn.execute(
            "SELECT COUNT(*) FROM users WHERE substr(last_seen,1,10)=?", (today,)
        ).fetchone()[0])
    except Exception:
        users = downloads = banned = today_downloads = today_users = 0
    finally:
        if conn:
            conn.close()
    return (
        "🎛️ <b>لوحة القيادة الإدارية</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 <b>الحالة: ONLINE</b>\n\n"
        f"👥 المستخدمون: <b>{users}</b>\n"
        f"🚫 المحظورون: <b>{banned}</b>\n"
        f"📥 إجمالي التحميلات: <b>{downloads}</b>\n"
        f"📅 تحميلات اليوم: <b>{today_downloads}</b>\n"
        f"🟢 نشطون اليوم: <b>{today_users}</b>\n\n"
        "🩺 الصحة • 🤖 العمليات • 📡 المراقبة • 🧾 التدقيق • 🛡️ الصلاحيات\n\n"
        "اختر القسم المطلوب:"
    )


def _records_text(get_db):
    """Render the top-level downloads/data workspace without relying on a missing helper."""
    conn = None
    try:
        conn = get_db()
        total_downloads = int(conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0])
        total_users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        today = _now()[:10]
        today_downloads = int(conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE substr(created_at,1,10)=?", (today,)
        ).fetchone()[0])
        websites = int(conn.execute(
            "SELECT COUNT(DISTINCT website) FROM downloads WHERE website IS NOT NULL AND TRIM(website) <> ''"
        ).fetchone()[0])
    except Exception:
        total_downloads = today_downloads = websites = 0
        total_users = 0
    finally:
        if conn:
            conn.close()

    return (
        "📥 <b>التنزيلات والبيانات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 إجمالي عمليات التحميل: <b>{total_downloads}</b>\n"
        f"📅 تحميلات اليوم: <b>{today_downloads}</b>\n"
        f"🌐 المنصات المسجلة: <b>{websites}</b>\n"
        f"👥 المستخدمون المسجلون: <b>{total_users}</b>\n\n"
        "اختر أداة البيانات المطلوبة:"
    )


async def admin_control_center_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id, "center.view"):
        return
    audit(get_db, owner_id, "open_control_center")
    await query.edit_message_text(_home_text(get_db), parse_mode="HTML", reply_markup=admin_keyboard())
    raise ApplicationHandlerStop
