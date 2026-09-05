from pathlib import Path
import re

BOT = Path("bot.py")
MARKER = "# ALIBOT_ADMIN_CONTROL_CENTER_V1"


def one(text, pattern, replacement, label, flags=0):
    new, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return new


def main():
    text = BOT.read_text(encoding="utf-8")
    if MARKER in text:
        print("Admin Control Center already applied")
        return

    keyboard = '''def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 مركز التحكم", callback_data="admin_dashboard_30")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_0"), InlineKeyboardButton("📥 التحميلات", callback_data="admin_recent_downloads")],
        [InlineKeyboardButton("📈 التحليلات", callback_data="admin_stats"), InlineKeyboardButton("🌐 المنصات", callback_data="admin_top_websites")],
        [InlineKeyboardButton("📢 الإذاعة", callback_data="admin_broadcast"), InlineKeyboardButton("🤖 الذكاء الاصطناعي", callback_data="admin_ai")],
        [InlineKeyboardButton("🚨 صحة النظام", callback_data="admin_health"), InlineKeyboardButton("🧾 سجل الإدارة", callback_data="admin_audit")],
        [InlineKeyboardButton("🛡️ الأمان", callback_data="admin_security"), InlineKeyboardButton("🧹 التخزين", callback_data="admin_storage")],
    ])
'''
    text = one(text, r"def admin_keyboard\(\):.*?\n\n\nasync def admin_command", keyboard + "\n\nasync def admin_command", "admin keyboard", re.S)

    audit_schema = '''    # --------------------------------------------------------
    # سجل عمليات الإدارة
    # --------------------------------------------------------
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_created_at ON admin_audit_logs(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_admin_id ON admin_audit_logs(admin_id)")

'''
    text = one(text, r"(    # --------------------------------------------------------\n    # جدول رسائل الإعلانات المرسلة\n    # --------------------------------------------------------\n)", audit_schema + r"\1", "audit schema")

    funcs = r'''

# ============================================================
# AliBot Admin Control Center V1
# ============================================================

def admin_audit_log(admin_id, action, target_id=None, details=None):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO admin_audit_logs (admin_id, action, target_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(admin_id), str(action)[:200], target_id, "" if details is None else str(details)[:1000], datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Admin audit log failed: %s", type(exc).__name__)


def _admin_health_snapshot():
    disk = shutil.disk_usage("/app")
    db_ok = False
    try:
        conn = get_db()
        conn.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        pass
    return {
        "database": db_ok,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "deno": bool(shutil.which("deno")),
        "free_gb": disk.free / (1024 ** 3),
        "total_gb": disk.total / (1024 ** 3),
    }


async def admin_health_callback(update, context):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    h = _admin_health_snapshot()
    mark = lambda v: "🟢" if v else "🔴"
    text = (
        "🚨 صحة النظام\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{mark(h['database'])} قاعدة البيانات\n"
        f"{mark(h['ffmpeg'])} FFmpeg\n"
        f"{mark(h['ffprobe'])} FFprobe\n"
        f"{mark(h['deno'])} Deno\n\n"
        f"💾 المساحة الحرة: {h['free_gb']:.2f} GB\n"
        f"📦 إجمالي التخزين: {h['total_gb']:.2f} GB\n\n"
        "ℹ️ هذه الفحوصات للقراءة فقط."
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تحديث", callback_data="admin_health")],
            [InlineKeyboardButton("🧾 سجل الإدارة", callback_data="admin_audit"), InlineKeyboardButton("🔙 الرئيسية", callback_data="admin_home")],
        ]),
    )


async def admin_audit_callback(update, context):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    conn = get_db()
    rows = conn.execute(
        "SELECT admin_id, action, target_id, details, created_at FROM admin_audit_logs ORDER BY id DESC LIMIT 15"
    ).fetchall()
    conn.close()
    text = "🧾 سجل الإدارة\n━━━━━━━━━━━━━━━━━━━━\n\n"
    if not rows:
        text += "لا توجد عمليات إدارية مسجلة بعد."
    else:
        for row in rows:
            target = f" → {row['target_id']}" if row["target_id"] else ""
            details = f" | {row['details']}" if row["details"] else ""
            text += f"👑 {row['admin_id']} | {row['action']}{target}\n🕐 {row['created_at']}{details}\n──────────────\n"
    await query.edit_message_text(
        text[:3900],
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تحديث", callback_data="admin_audit")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="admin_home")],
        ]),
    )


async def admin_security_callback(update, context):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    admin_audit_log(ADMIN_ID, "view_security")
    text = (
        "🛡️ مركز الأمان\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "👑 المالك: مفعل\n"
        f"🆔 Owner ID: {ADMIN_ID}\n\n"
        "🔐 الوصول الحالي: Owner-only\n"
        "🧾 سجل العمليات: مفعل\n\n"
        "📌 نظام الأدوار Viewer / Moderator / Analyst سيضاف في المرحلة التالية بصلاحيات دقيقة."
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🧾 سجل الإدارة", callback_data="admin_audit")],
            [InlineKeyboardButton("🔙 الرئيسية", callback_data="admin_home")],
        ]),
    )

# End AliBot Admin Control Center V1
'''

    anchor = '# بدء التشغيل\n\nif __name__ == "__main__":\n    main()'
    text = one(text, re.escape(anchor), funcs + "\n\n" + anchor, "final insertion")

    handlers = '''    # ========================================================
    # AliBot Admin Control Center V1
    # ========================================================
    app.add_handler(CallbackQueryHandler(admin_health_callback, pattern=r"^admin_health$"))
    app.add_handler(CallbackQueryHandler(admin_audit_callback, pattern=r"^admin_audit$"))
    app.add_handler(CallbackQueryHandler(admin_security_callback, pattern=r"^admin_security$"))

'''
    text = one(text, r"(    # ========================================================\n    # التحميل\n    # ========================================================\n)", handlers + r"\1", "handler insertion")

    dashboard_anchor = '''    await query.edit_message_text(
        admin_dashboard_text(data),
        reply_markup=admin_dashboard_keyboard(days)
    )
'''
    dashboard_logged = '''    admin_audit_log(ADMIN_ID, "view_dashboard", details=f"period={days}d")

''' + dashboard_anchor
    text = one(text, re.escape(dashboard_anchor), dashboard_logged, "dashboard audit hook")

    BOT.write_text(MARKER + "\n" + text, encoding="utf-8")
    print("Admin Control Center V1 applied successfully")


if __name__ == "__main__":
    main()
