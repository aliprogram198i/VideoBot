import json
import shutil
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes


def _now():
    return datetime.now().isoformat(timespec="seconds")


def register_admin_control_center(app, get_db, owner_id):
    init_admin_control_center(get_db, owner_id)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_control_center_callback(u, c, get_db, owner_id),
        pattern=r"^admin_control_center$",
    ))
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_health_callback(u, c, get_db, owner_id),
        pattern=r"^admin_health$",
    ))
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_audit_callback(u, c, get_db, owner_id),
        pattern=r"^admin_audit$",
    ))
    app.add_handler(CallbackQueryHandler(
        lambda u, c: admin_roles_callback(u, c, get_db, owner_id),
        pattern=r"^admin_roles$",
    ))


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
    cur.execute("""
        INSERT INTO admin_roles (user_id, role, permissions, created_at, updated_at)
        VALUES (?, 'owner', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET role='owner', updated_at=excluded.updated_at
    """, (owner_id, json.dumps({'*': True}), _now(), _now()))
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


def _authorized(update, owner_id):
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🩺 صحة النظام", callback_data="admin_health")],
        [InlineKeyboardButton("🧾 سجل التدقيق", callback_data="admin_audit")],
        [InlineKeyboardButton("🛡️ الأدوار والصلاحيات", callback_data="admin_roles")],
        [InlineKeyboardButton("📊 لوحة الإحصائيات", callback_data="admin_dashboard_30")],
        [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_home")],
    ])


def _home_text():
    return (
        "🎛️ <b>مركز التحكم الإداري</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 النظام الإداري يعمل\n"
        "🔐 الوصول محمي بمالك البوت\n"
        "🧾 التدقيق الإداري مفعّل\n"
        "🛡️ نظام الأدوار جاهز للتوسع\n\n"
        "اختر القسم المطلوب:"
    )


async def admin_control_center_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    audit(get_db, owner_id, "open_control_center")
    await query.edit_message_text(_home_text(), parse_mode="HTML", reply_markup=_keyboard())


async def admin_health_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    conn = None
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        db_status, db_detail = "🟢", "متصل ويستجيب"
    except Exception as exc:
        db_status, db_detail = "🔴", type(exc).__name__
    finally:
        if conn:
            conn.close()
    try:
        _, _, free = shutil.disk_usage("/")
        free_gb = free / (1024 ** 3)
        disk_status = "🟢" if free_gb >= 1 else "🟠" if free_gb >= 0.25 else "🔴"
        disk_detail = f"{free_gb:.2f} GB متاح"
    except Exception:
        disk_status, disk_detail = "⚪", "غير متاح"
    audit(get_db, owner_id, "view_system_health")
    text = (
        "🩺 <b>صحة النظام</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🗄️ قاعدة البيانات: {db_status} {db_detail}\n"
        f"💾 التخزين: {disk_status} {disk_detail}\n"
        "🤖 خدمة البوت: 🟢 تعمل\n"
        f"🕒 وقت الفحص: {_now()}\n\n"
        "ℹ️ الفحص تشخيصي فقط ولا يغيّر إعدادات النظام."
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_health")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
    ]))


async def admin_audit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    conn = get_db()
    rows = conn.execute(
        "SELECT admin_id, action, target_id, details, created_at "
        "FROM admin_audit_logs ORDER BY id DESC LIMIT 15"
    ).fetchall()
    conn.close()
    lines = ["🧾 <b>سجل التدقيق الإداري</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not rows:
        lines.append("لا توجد عمليات مسجلة بعد.")
    else:
        for row in rows:
            target = f" → {row['target_id']}" if row['target_id'] is not None else ""
            detail = f" — {row['details']}" if row['details'] else ""
            lines.append(f"• <code>{row['created_at']}</code> | {row['action']}{target}{detail}")
    audit(get_db, owner_id, "view_audit_log")
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_audit")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
    ]))


async def admin_roles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    conn = get_db()
    rows = conn.execute(
        "SELECT role, COUNT(*) AS count FROM admin_roles GROUP BY role ORDER BY count DESC"
    ).fetchall()
    conn.close()
    lines = ["🛡️ <b>الأدوار والصلاحيات</b>", "━━━━━━━━━━━━━━━━━━━━", "", "👑 Owner: صلاحية كاملة"]
    for row in rows:
        if row['role'] != 'owner':
            lines.append(f"• {row['role']}: {row['count']}")
    lines += ["", "🔒 تعديل الأدوار غير مفعّل تلقائياً في هذه المرحلة لحماية لوحة الإدارة."]
    audit(get_db, owner_id, "view_admin_roles")
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
    ]))
