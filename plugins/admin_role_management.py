"""Safe, fixed-preset administration role management.

Role changes are intentionally command-driven and fail closed. No arbitrary
permission JSON is accepted from Telegram input, and the configured owner can
never be modified or removed through this workflow.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from .admin_common import authorize
from .admin_control_center import audit


ROLE_PERMISSIONS = {
    "viewer": frozenset({
        "center.view",
        "users.view",
        "history.view",
        "analytics.view",
        "operations.view",
        "system.health",
        "audit.view",
        "roles.view",
    }),
    "operator": frozenset({
        "center.view",
        "users.view",
        "history.view",
        "history.clear",
        "analytics.view",
        "operations.view",
        "system.health",
        "audit.view",
        "security.view",
    }),
    "publisher": frozenset({
        "center.view",
        "broadcast.send",
    }),
    "backup": frozenset({
        "center.view",
        "database.backup",
    }),
    "role_manager": frozenset({
        "center.view",
        "roles.view",
        "roles.manage",
    }),
}

_USER_ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _permission_json(role: str) -> str:
    return json.dumps({permission: True for permission in ROLE_PERMISSIONS[role]}, separators=(",", ":"))


def _table_columns(conn) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(admin_roles)").fetchall()}


def _upsert_role(get_db, user_id: int, role: str) -> None:
    permissions = _permission_json(role)
    now = _now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        columns = _table_columns(conn)
        if not {"user_id", "role", "permissions", "updated_at"}.issubset(columns):
            raise RuntimeError("admin_roles schema is incomplete")

        if "created_at" in columns:
            conn.execute(
                """INSERT INTO admin_roles
                   (user_id, role, permissions, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       role=excluded.role,
                       permissions=excluded.permissions,
                       updated_at=excluded.updated_at""",
                (user_id, role, permissions, now, now),
            )
        else:
            conn.execute(
                """INSERT INTO admin_roles
                   (user_id, role, permissions, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       role=excluded.role,
                       permissions=excluded.permissions,
                       updated_at=excluded.updated_at""",
                (user_id, role, permissions, now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _remove_role(get_db, user_id: int) -> bool:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("DELETE FROM admin_roles WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _role_rows(get_db):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT user_id, role, updated_at FROM admin_roles ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()


def _format_roles(rows) -> str:
    lines = ["🛡️ <b>الأدوار والصلاحيات</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not rows:
        lines.append("لا توجد أدوار مسجلة.")
    else:
        for row in rows:
            lines.append(f"• 👤 <code>{row['user_id']}</code> — <b>{row['role']}</b> — {row['updated_at']}")
    lines += [
        "",
        "الأدوار المتاحة: <code>viewer</code> • <code>operator</code> • <code>publisher</code> • <code>backup</code> • <code>role_manager</code>",
        "",
        "الاستخدام:",
        "<code>/adminrole set USER_ID ROLE</code>",
        "<code>/adminrole remove USER_ID</code>",
        "<code>/adminrole list</code>",
    ]
    return "\n".join(lines)[:3900]


def _authorized(update: Update, get_db, owner_id: int) -> bool:
    return authorize(update, get_db, owner_id, "roles.manage")


async def admin_role_command(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not _authorized(update, get_db, owner_id):
        await message.reply_text("⛔ لا تملك صلاحية إدارة الأدوار.")
        return

    args = list(context.args or [])
    action = args[0].lower() if args else "list"

    if action == "list" and len(args) == 1:
        await message.reply_text(_format_roles(_role_rows(get_db)), parse_mode="HTML")
        audit(get_db, int(user.id), "list_admin_roles")
        return

    if action == "set" and len(args) == 3:
        raw_user_id, role = args[1], args[2].lower()
        if not _USER_ID_RE.fullmatch(raw_user_id):
            await message.reply_text("❌ معرّف المستخدم غير صالح.")
            return
        if role not in ROLE_PERMISSIONS:
            await message.reply_text("❌ الدور غير صالح. استخدم أحد الأدوار المعروضة في /adminrole list.")
            return
        target_id = int(raw_user_id)
        if target_id == int(owner_id):
            await message.reply_text("🛡️ لا يمكن تغيير دور مالك البوت.")
            return
        try:
            _upsert_role(get_db, target_id, role)
            audit(get_db, int(user.id), "set_admin_role", target_id, role)
        except Exception:
            await message.reply_text("❌ تعذر حفظ الدور بسبب مشكلة في مخطط قاعدة البيانات.")
            return
        await message.reply_text(f"✅ تم تعيين الدور <b>{role}</b> للمستخدم <code>{target_id}</code>.", parse_mode="HTML")
        return

    if action == "remove" and len(args) == 2:
        raw_user_id = args[1]
        if not _USER_ID_RE.fullmatch(raw_user_id):
            await message.reply_text("❌ معرّف المستخدم غير صالح.")
            return
        target_id = int(raw_user_id)
        if target_id == int(owner_id):
            await message.reply_text("🛡️ لا يمكن إزالة دور مالك البوت.")
            return
        try:
            removed = _remove_role(get_db, target_id)
            audit(get_db, int(user.id), "remove_admin_role", target_id)
        except Exception:
            await message.reply_text("❌ تعذر إزالة الدور.")
            return
        await message.reply_text("✅ تم حذف الدور." if removed else "ℹ️ لا يوجد دور مسجل لهذا المستخدم.")
        return

    await message.reply_text(
        "ℹ️ الاستخدام الصحيح:\n"
        "<code>/adminrole list</code>\n"
        "<code>/adminrole set USER_ID ROLE</code>\n"
        "<code>/adminrole remove USER_ID</code>",
        parse_mode="HTML",
    )


def register_admin_role_management(app, get_db, owner_id: int) -> None:
    app.add_handler(
        CommandHandler(
            "adminrole",
            lambda update, context: admin_role_command(update, context, get_db, owner_id),
        ),
        group=-200,
    )
