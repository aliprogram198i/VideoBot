"""Read-only operations views for the canonical AliBot admin layer.

This module intentionally uses only existing database data. It does not alter the
 download pipeline, introduce migrations, or write operational state. Incident
 telemetry is displayed only when an existing compatible table is present.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

_MAX_TEXT = 3900


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _now() -> datetime:
    return datetime.now()


def _cutoff(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def _safe_count(conn, sql: str, params: tuple = ()) -> int:
    try:
        return int(conn.execute(sql, params).fetchone()[0] or 0)
    except Exception:
        return 0


def _dashboard_data(get_db) -> dict[str, Any]:
    conn = get_db()
    try:
        users = _safe_count(conn, "SELECT COUNT(*) FROM users")
        banned = _safe_count(conn, "SELECT COALESCE(SUM(is_banned),0) FROM users")
        downloads = _safe_count(conn, "SELECT COUNT(*) FROM downloads")
        today_downloads = _safe_count(conn, "SELECT COUNT(*) FROM downloads WHERE created_at >= ?", (_cutoff(1),))
        week_downloads = _safe_count(conn, "SELECT COUNT(*) FROM downloads WHERE created_at >= ?", (_cutoff(7),))
        month_downloads = _safe_count(conn, "SELECT COUNT(*) FROM downloads WHERE created_at >= ?", (_cutoff(30),))
        active_today = _safe_count(conn, "SELECT COUNT(*) FROM users WHERE last_seen >= ?", (_cutoff(1),))
        active_week = _safe_count(conn, "SELECT COUNT(*) FROM users WHERE last_seen >= ?", (_cutoff(7),))
        videos = _safe_count(conn, "SELECT COUNT(*) FROM downloads WHERE media_type = 'video'")
        audio = _safe_count(conn, "SELECT COUNT(*) FROM downloads WHERE media_type = 'audio'")
        platforms = _safe_count(conn, "SELECT COUNT(DISTINCT website) FROM downloads WHERE website IS NOT NULL AND TRIM(website) <> ''")
        top = conn.execute(
            "SELECT website, COUNT(*) AS count FROM downloads "
            "WHERE website IS NOT NULL AND TRIM(website) <> '' "
            "GROUP BY website ORDER BY count DESC LIMIT 5"
        ).fetchall()
        return {
            "users": users, "banned": banned, "downloads": downloads,
            "today_downloads": today_downloads, "week_downloads": week_downloads,
            "month_downloads": month_downloads, "active_today": active_today,
            "active_week": active_week, "videos": videos, "audio": audio,
            "platforms": platforms, "top": top,
        }
    finally:
        conn.close()


def _dashboard_text(data: dict[str, Any]) -> str:
    lines = [
        "📊 <b>مركز العمليات — لوحة القيادة</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 المستخدمون: <b>{data['users']}</b>  |  🚫 محظورون: <b>{data['banned']}</b>",
        f"🟢 نشطون اليوم: <b>{data['active_today']}</b>  |  7 أيام: <b>{data['active_week']}</b>",
        "",
        f"📥 العمليات المسجلة: <b>{data['downloads']}</b>",
        f"📅 اليوم: <b>{data['today_downloads']}</b>  |  7 أيام: <b>{data['week_downloads']}</b>  |  30 يوم: <b>{data['month_downloads']}</b>",
        f"🎥 فيديو: <b>{data['videos']}</b>  |  🎵 صوت: <b>{data['audio']}</b>",
        f"🌐 منصات مسجلة: <b>{data['platforms']}</b>",
        "",
        "🌐 <b>أكثر المنصات</b>",
    ]
    if data["top"]:
        for row in data["top"]:
            lines.append(f"• {html.escape(str(row['website'] or 'غير معروف'))}: {int(row['count'])}")
    else:
        lines.append("• لا توجد بيانات منصات بعد.")
    return "\n".join(lines)


def _dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 التنزيلات", callback_data="admin_ops_downloads"),
         InlineKeyboardButton("🩺 صحة المنصات", callback_data="admin_ops_platforms")],
        [InlineKeyboardButton("🚨 مركز الحوادث", callback_data="admin_ops_incidents")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


async def dashboard_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    await query.edit_message_text(_dashboard_text(_dashboard_data(get_db)), parse_mode="HTML", reply_markup=_dashboard_keyboard())
    raise ApplicationHandlerStop


def _downloads_data(get_db) -> tuple[list[Any], int]:
    conn = get_db()
    try:
        total = _safe_count(conn, "SELECT COUNT(*) FROM downloads")
        rows = conn.execute(
            "SELECT user_id, website, media_type, quality, title, created_at "
            "FROM downloads ORDER BY id DESC LIMIT 20"
        ).fetchall()
        return rows, total
    finally:
        conn.close()


def _downloads_text(rows: list[Any], total: int) -> str:
    lines = [
        "📥 <b>مركز عمليات التنزيل</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🧾 إجمالي العمليات المسجلة: <b>{total}</b>",
        "📌 آخر 20 عملية:",
        "",
    ]
    if not rows:
        lines.append("لا توجد عمليات تحميل مسجلة.")
    else:
        for index, row in enumerate(rows, 1):
            media = "🎥" if row["media_type"] == "video" else "🎵" if row["media_type"] == "audio" else "📦"
            website = html.escape(str(row["website"] or "غير معروف"))
            quality = html.escape(str(row["quality"] or ""))
            title = html.escape(str(row["title"] or "بدون عنوان"))[:80]
            created = html.escape(str(row["created_at"] or ""))
            lines.append(f"{index}. {media} <b>{title}</b> — {website} — {quality} — 👤 {row['user_id']} — {created}")
            if len("\n".join(lines)) > _MAX_TEXT:
                break
    return "\n".join(lines)[:_MAX_TEXT]


async def downloads_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    rows, total = _downloads_data(get_db)
    await query.edit_message_text(_downloads_text(rows, total), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_ops_downloads")],
        [InlineKeyboardButton("📊 لوحة القيادة", callback_data="admin_ops_dashboard")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ]), disable_web_page_preview=True)
    raise ApplicationHandlerStop


def _platform_rows(get_db) -> list[Any]:
    conn = get_db()
    try:
        return conn.execute(
            "SELECT website, COUNT(*) AS total, "
            "MAX(created_at) AS last_seen "
            "FROM downloads WHERE website IS NOT NULL AND TRIM(website) <> '' "
            "GROUP BY website ORDER BY total DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()


async def platforms_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    rows = _platform_rows(get_db)
    lines = [
        "🩺 <b>صحة المنصات</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "ℹ️ الحالة هنا مبنية على النشاط المسجل؛ لا يتم اختلاق نسبة نجاح أو فشل غير موجودة في قاعدة البيانات.",
        "",
    ]
    if not rows:
        lines.append("لا توجد بيانات منصات مسجلة بعد.")
    else:
        for index, row in enumerate(rows, 1):
            website = html.escape(str(row["website"] or "غير معروف"))
            total = int(row["total"] or 0)
            last_seen = html.escape(str(row["last_seen"] or "غير متاح"))
            lines.append(f"{index}. 🌐 <b>{website}</b> — {total} عملية — آخر نشاط: {last_seen}")
    await query.edit_message_text("\n".join(lines)[:_MAX_TEXT], parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_ops_platforms")],
        [InlineKeyboardButton("📊 لوحة القيادة", callback_data="admin_ops_dashboard")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ]))
    raise ApplicationHandlerStop


def _incident_source(get_db) -> str | None:
    """Return an existing error/incident table without creating one."""
    conn = get_db()
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    for candidate in ("download_errors", "error_logs", "errors", "incidents", "error_events"):
        if candidate in names:
            return candidate
    return None


async def incidents_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    source = _incident_source(get_db)
    lines = [
        "🚨 <b>مركز الحوادث</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if source is None:
        lines += [
            "🟢 لا يوجد مصدر حوادث مستقل متصل حالياً.",
            "",
            "هذه الصفحة متعمدة أن تكون صادقة: لا يتم إنشاء بيانات وهمية ولا تسجيل أخطاء من داخل لوحة الإدارة.",
            "",
            "📌 عند ربط Telemetry/Incident Store في مرحلة لاحقة ستعرض الصفحة التجميع، التكرار، المنصة المتأثرة، الشدة والحالة.",
        ]
    else:
        # Schema varies between deployments; inspect only common columns and never mutate it.
        conn = get_db()
        try:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({source})").fetchall()}
            preferred = [c for c in ("created_at", "timestamp", "occurred_at") if c in columns]
            order = preferred[0] if preferred else None
            if order:
                count = _safe_count(conn, f"SELECT COUNT(*) FROM {source}")
                recent = conn.execute(f"SELECT * FROM {source} ORDER BY {order} DESC LIMIT 10").fetchall()
            else:
                count = _safe_count(conn, f"SELECT COUNT(*) FROM {source}")
                recent = []
        finally:
            conn.close()
        lines += [f"🧾 مصدر الحوادث: <code>{html.escape(source)}</code>", f"📌 إجمالي السجلات: <b>{count}</b>", ""]
        if recent:
            for row in recent:
                values = [str(v) for v in row]
                lines.append("• " + html.escape(" | ".join(values)[:260]))
        else:
            lines.append("لا توجد سجلات حديثة قابلة للعرض.")
    await query.edit_message_text("\n".join(lines)[:_MAX_TEXT], parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_ops_incidents")],
        [InlineKeyboardButton("📊 لوحة القيادة", callback_data="admin_ops_dashboard")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ]))
    raise ApplicationHandlerStop


def register_admin_operations_center(app: Any, get_db, owner_id: int) -> None:
    """Register the isolated operations-center callbacks exactly once."""
    app.add_handler(CallbackQueryHandler(lambda u, c: dashboard_callback(u, c, get_db, owner_id), pattern=r"^admin_ops_dashboard$"), group=-150)
    app.add_handler(CallbackQueryHandler(lambda u, c: downloads_callback(u, c, get_db, owner_id), pattern=r"^admin_ops_downloads$"), group=-150)
    app.add_handler(CallbackQueryHandler(lambda u, c: platforms_callback(u, c, get_db, owner_id), pattern=r"^admin_ops_platforms$"), group=-150)
    app.add_handler(CallbackQueryHandler(lambda u, c: incidents_callback(u, c, get_db, owner_id), pattern=r"^admin_ops_incidents$"), group=-150)
