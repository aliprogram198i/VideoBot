"""Read-only operational observability for AliBot.

This module consumes the existing Smart telemetry database without changing its
schema, extraction behavior, fallback policy, or user data. It provides one
callback namespace with four views: overview, platform health, extraction
performance, and failure intelligence.
"""

from __future__ import annotations

import html
import os
import sqlite3
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

_CALLBACK = "admin_observability"
_MAX_TEXT = 3900


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _telemetry_path() -> Path:
    configured = os.getenv("SMART_DATA_DIR")
    if configured:
        return Path(configured).expanduser() / "smart_learning.db"
    railway_data = Path("/app/data")
    if railway_data.is_dir():
        return railway_data / "smart" / "smart_learning.db"
    return Path(".smart_data") / "smart_learning.db"


def _empty() -> dict[str, Any]:
    return {
        "available": False,
        "telemetry": 0,
        "outcomes": 0,
        "successes": 0,
        "failures": 0,
        "success_rate": None,
        "avg_ms": None,
        "platforms": [],
        "kinds": [],
        "failures_by_reason": [],
        "last_24h": 0,
        "last_24h_success_rate": None,
    }


def collect_observability() -> dict[str, Any]:
    """Read existing Smart telemetry in SQLite read-only mode."""
    path = _telemetry_path()
    if not path.is_file():
        return _empty()
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except (OSError, sqlite3.Error):
        return _empty()
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        required = {"smart_telemetry", "smart_outcomes"}
        if not required.issubset(tables):
            return _empty()

        telemetry = int(conn.execute("SELECT COUNT(*) FROM smart_telemetry").fetchone()[0])
        outcomes = int(conn.execute("SELECT COUNT(*) FROM smart_outcomes").fetchone()[0])
        successes = int(conn.execute("SELECT COUNT(*) FROM smart_outcomes WHERE success=1").fetchone()[0])
        failures = outcomes - successes
        avg_ms = conn.execute("SELECT AVG(extraction_ms) FROM smart_telemetry").fetchone()[0]

        platforms = conn.execute(
            """SELECT COALESCE(t.source_host,'unknown') AS host,
                      COUNT(o.id) AS outcomes,
                      SUM(CASE WHEN o.success=1 THEN 1 ELSE 0 END) AS successes,
                      AVG(t.extraction_ms) AS avg_ms
               FROM smart_telemetry t
               LEFT JOIN smart_outcomes o ON o.telemetry_id=t.id
               GROUP BY t.source_host ORDER BY outcomes DESC, t.source_host ASC LIMIT 10"""
        ).fetchall()
        kinds = conn.execute(
            """SELECT COALESCE(o.selected_kind,t.best_kind,'unknown') AS kind,
                      COUNT(o.id) AS outcomes,
                      SUM(CASE WHEN o.success=1 THEN 1 ELSE 0 END) AS successes,
                      AVG(t.extraction_ms) AS avg_ms
               FROM smart_telemetry t
               LEFT JOIN smart_outcomes o ON o.telemetry_id=t.id
               GROUP BY kind ORDER BY outcomes DESC, kind ASC LIMIT 10"""
        ).fetchall()
        failure_rows = conn.execute(
            """SELECT COALESCE(failure_reason,'unknown') AS reason, COUNT(*) AS total
               FROM smart_outcomes WHERE success=0
               GROUP BY failure_reason ORDER BY total DESC, reason ASC LIMIT 10"""
        ).fetchall()
        last_24h = int(conn.execute(
            "SELECT COUNT(*) FROM smart_telemetry WHERE created_at >= datetime('now','-24 hours')"
        ).fetchone()[0])
        last_24h_outcomes = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS successes
               FROM smart_outcomes WHERE created_at >= datetime('now','-24 hours')"""
        ).fetchone()
    except sqlite3.Error:
        return _empty()
    finally:
        conn.close()

    rate = round(successes * 100 / outcomes, 1) if outcomes else None
    recent_total = int(last_24h_outcomes["total"] or 0)
    recent_successes = int(last_24h_outcomes["successes"] or 0)
    recent_rate = round(recent_successes * 100 / recent_total, 1) if recent_total else None
    return {
        "available": True,
        "telemetry": telemetry,
        "outcomes": outcomes,
        "successes": successes,
        "failures": failures,
        "success_rate": rate,
        "avg_ms": round(float(avg_ms), 1) if avg_ms is not None else None,
        "platforms": [dict(row) for row in platforms],
        "kinds": [dict(row) for row in kinds],
        "failures_by_reason": [dict(row) for row in failure_rows],
        "last_24h": last_24h,
        "last_24h_success_rate": recent_rate,
    }


def _pct(successes: Any, outcomes: Any) -> str:
    total = int(outcomes or 0)
    if not total:
        return "غير متاح"
    return f"{round(int(successes or 0) * 100 / total, 1)}%"


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 النظرة العامة", callback_data=f"{_CALLBACK}:overview"),
         InlineKeyboardButton("🌐 صحة المنصات", callback_data=f"{_CALLBACK}:health")],
        [InlineKeyboardButton("⚙️ أداء الاستخراج", callback_data=f"{_CALLBACK}:performance"),
         InlineKeyboardButton("🚨 ذكاء الفشل", callback_data=f"{_CALLBACK}:failures")],
        [InlineKeyboardButton("🔄 تحديث", callback_data=_CALLBACK),
         InlineKeyboardButton("🤖 العمليات الذكية", callback_data="admin_smart_operations")],
        [InlineKeyboardButton("🧠 Fallback Intelligence", callback_data="admin_fallback_intelligence")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


def _render(data: dict[str, Any], view: str) -> str:
    if not data["available"]:
        return (
            "📡 <b>مركز المراقبة الذكية</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "🟠 Smart telemetry غير متاح حاليًا.\n"
            "ℹ️ لم يتم إنشاء قاعدة القياس أو لا يمكن قراءتها.\n\n"
            "🔐 القراءة فقط — لم يتم تعديل أي بيانات."
        )

    title = {
        "overview": "📡 <b>مركز المراقبة الذكية</b>",
        "health": "🌐 <b>صحة المنصات</b>",
        "performance": "⚙️ <b>أداء الاستخراج</b>",
        "failures": "🚨 <b>ذكاء الفشل</b>",
    }.get(view, "📡 <b>مركز المراقبة الذكية</b>")
    lines = [title, "━━━━━━━━━━━━━━━━━━━━", ""]

    if view == "overview":
        lines += [
            f"📡 أحداث القياس: <b>{data['telemetry']}</b>",
            f"🔗 نتائج مكتملة: <b>{data['outcomes']}</b>",
            f"✅ نجاح: <b>{data['successes']}</b>  |  ❌ فشل: <b>{data['failures']}</b>",
            f"📈 معدل النجاح: <b>{data['success_rate'] if data['success_rate'] is not None else 'غير متاح'}%</b>" if data['success_rate'] is not None else "📈 معدل النجاح: <b>غير متاح</b>",
            f"⏱️ متوسط الاستخراج: <b>{data['avg_ms']} ms</b>" if data['avg_ms'] is not None else "⏱️ متوسط الاستخراج: <b>غير متاح</b>",
            f"🕐 أحداث آخر 24 ساعة: <b>{data['last_24h']}</b>",
            f"📈 نجاح آخر 24 ساعة: <b>{data['last_24h_success_rate']}%</b>" if data['last_24h_success_rate'] is not None else "📈 نجاح آخر 24 ساعة: <b>غير متاح</b>",
            "",
            "🧭 <b>التغطية</b>",
            f"🌐 منصات مسجلة: <b>{len(data['platforms'])}</b>",
            f"⚙️ أنماط استخراج مسجلة: <b>{len(data['kinds'])}</b>",
            f"🚨 أسباب فشل مسجلة: <b>{len(data['failures_by_reason'])}</b>",
        ]
    elif view == "health":
        lines.append("مقارنة المنصات تعتمد على Smart telemetry الموجود فقط؛ لا يتم اختلاق بيانات عند غياب النتائج.\n")
        if not data["platforms"]:
            lines.append("لا توجد بيانات منصات بعد.")
        else:
            for row in data["platforms"]:
                host = html.escape(str(row["host"]))
                lines.append(
                    f"• <b>{host}</b> — {int(row['outcomes'] or 0)} نتيجة — "
                    f"نجاح {_pct(row['successes'], row['outcomes'])} — "
                    f"متوسط {round(float(row['avg_ms']),1) if row['avg_ms'] is not None else '—'} ms"
                )
    elif view == "performance":
        if not data["kinds"]:
            lines.append("لا توجد بيانات أداء بعد.")
        else:
            for row in data["kinds"]:
                kind = html.escape(str(row["kind"]))
                lines.append(
                    f"• <b>{kind}</b> — {int(row['outcomes'] or 0)} نتيجة — "
                    f"نجاح {_pct(row['successes'], row['outcomes'])} — "
                    f"متوسط {round(float(row['avg_ms']),1) if row['avg_ms'] is not None else '—'} ms"
                )
    else:
        if not data["failures_by_reason"]:
            lines.append("🟢 لا توجد أسباب فشل مسجلة.")
        else:
            for row in data["failures_by_reason"]:
                reason = html.escape(str(row["reason"]))[:120]
                lines.append(f"• <b>{reason}</b> — {int(row['total'] or 0)}")
            lines += [
                "",
                "ℹ️ هذه الشاشة تشخّص الأنماط فقط؛ لا تعيد المحاولة تلقائيًا ولا تغيّر سياسات الإنتاج.",
            ]

    lines += ["", "🔐 <b>قراءة فقط</b> — لا تعديل على DB المستخدمين أو Downloader أو سياسات Smart."]
    return "\n".join(lines)[:_MAX_TEXT]


async def observability_callback(update: Update, context, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    data = collect_observability()
    raw = query.data or _CALLBACK
    view = raw.split(":", 1)[1] if ":" in raw else "overview"
    if view not in {"overview", "health", "performance", "failures"}:
        view = "overview"
    try:
        from .admin_control_center import audit
        audit(_get_db_from_context(context), owner_id, f"view_observability_{view}")
    except Exception:
        pass
    await query.edit_message_text(_render(data, view), parse_mode="HTML", reply_markup=_keyboard())
    raise ApplicationHandlerStop


def _get_db_from_context(context):
    getter = getattr(context, "bot_data", {}).get("get_db")
    if not callable(getter):
        raise RuntimeError("admin observability database handle unavailable")
    return getter


def register_admin_observability(app, get_db, owner_id: int) -> None:
    """Register the single observability callback owner."""
    app.bot_data["get_db"] = get_db
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: observability_callback(u, c, owner_id),
            pattern=rf"^{_CALLBACK}(?::(?:overview|health|performance|failures))?$",
        ),
        group=-160,
    )
