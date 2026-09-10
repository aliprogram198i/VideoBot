"""Read-only fallback intelligence views for the canonical AliBot admin layer.

This module reads the existing Smart telemetry database only. It does not create
it, alter extraction, alter fallback behavior, train models, promote policies,
or modify the main bot database.
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

_CALLBACK = "admin_fallback_intelligence"
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


def _safe_json(value: str | None) -> Any:
    try:
        return json.loads(value or "null")
    except Exception:
        return None


def _empty_data() -> dict[str, Any]:
    return {
        "telemetry_total": 0,
        "outcomes_total": 0,
        "successes": 0,
        "failures": 0,
        "success_rate": None,
        "avg_ms": None,
        "hosts": [],
        "kinds": [],
        "failures_by_reason": [],
        "diagnostics": [],
        "production_policy": "غير متاح",
        "policy_candidates": 0,
        "evaluations": 0,
    }


def collect_fallback_intelligence() -> dict[str, Any]:
    """Read Smart telemetry in SQLite read-only mode; never initialize the DB."""
    path = _telemetry_path()
    if not path.is_file():
        return _empty_data()

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except (OSError, sqlite3.Error):
        return _empty_data()

    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required = {"smart_telemetry", "smart_outcomes", "smart_policy_versions", "smart_evaluations"}
        if not required.issubset(tables):
            return _empty_data()

        telemetry_total = int(conn.execute("SELECT COUNT(*) FROM smart_telemetry").fetchone()[0])
        outcomes_total = int(conn.execute("SELECT COUNT(*) FROM smart_outcomes").fetchone()[0])
        successes = int(conn.execute("SELECT COUNT(*) FROM smart_outcomes WHERE success=1").fetchone()[0])
        failures = int(conn.execute("SELECT COUNT(*) FROM smart_outcomes WHERE success=0").fetchone()[0])
        avg_ms = conn.execute("SELECT AVG(extraction_ms) FROM smart_telemetry").fetchone()[0]

        host_rows = conn.execute(
            """SELECT COALESCE(source_host,'unknown') AS host, COUNT(*) AS total
               FROM smart_telemetry GROUP BY source_host ORDER BY total DESC LIMIT 8"""
        ).fetchall()
        kind_rows = conn.execute(
            """SELECT COALESCE(selected_kind,'unknown') AS kind, COUNT(*) AS total,
                      SUM(success) AS successes
               FROM smart_outcomes GROUP BY selected_kind ORDER BY total DESC LIMIT 8"""
        ).fetchall()
        failure_rows = conn.execute(
            """SELECT COALESCE(failure_reason,'unknown') AS reason, COUNT(*) AS total
               FROM smart_outcomes WHERE success=0
               GROUP BY failure_reason ORDER BY total DESC LIMIT 6"""
        ).fetchall()

        diagnostic_counter: Counter[str] = Counter()
        for row in conn.execute("SELECT diagnostics_json FROM smart_telemetry ORDER BY id DESC LIMIT 500").fetchall():
            diagnostics = _safe_json(row[0])
            if isinstance(diagnostics, list):
                for item in diagnostics:
                    name = str(item).split(":", 1)[0].strip()
                    if name:
                        diagnostic_counter[name] += 1

        production = conn.execute(
            "SELECT version FROM smart_policy_versions WHERE status='production' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        policy_version = str(production[0]) if production else "غير متاح"
        policy_candidates = int(conn.execute("SELECT COUNT(*) FROM smart_policy_versions WHERE status='candidate'").fetchone()[0])
        evaluations = int(conn.execute("SELECT COUNT(*) FROM smart_evaluations").fetchone()[0])
    except sqlite3.Error:
        return _empty_data()
    finally:
        conn.close()

    outcome_rate = round(successes * 100 / outcomes_total, 1) if outcomes_total else None
    return {
        "telemetry_total": telemetry_total,
        "outcomes_total": outcomes_total,
        "successes": successes,
        "failures": failures,
        "success_rate": outcome_rate,
        "avg_ms": round(float(avg_ms), 1) if avg_ms is not None else None,
        "hosts": host_rows,
        "kinds": kind_rows,
        "failures_by_reason": failure_rows,
        "diagnostics": diagnostic_counter.most_common(6),
        "production_policy": policy_version,
        "policy_candidates": policy_candidates,
        "evaluations": evaluations,
    }


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data=_CALLBACK)],
        [InlineKeyboardButton("🤖 Smart Operations", callback_data="admin_smart_operations")],
        [InlineKeyboardButton("📊 مركز العمليات", callback_data="admin_ops_dashboard")],
        [InlineKeyboardButton("🧾 سجل التدقيق", callback_data="admin_audit")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


def _render(data: dict[str, Any]) -> str:
    success = f"{data['success_rate']}%" if data["success_rate"] is not None else "غير متاح — لا توجد نتائج مرتبطة"
    latency = f"{data['avg_ms']} ms" if data["avg_ms"] is not None else "غير متاح"
    lines = [
        "🧠 <b>Fallback Intelligence</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📡 Telemetry: <b>{data['telemetry_total']}</b>",
        f"🔗 نتائج مرتبطة: <b>{data['outcomes_total']}</b>",
        f"✅ نجاح النتائج: <b>{data['successes']}</b>  |  ❌ فشل: <b>{data['failures']}</b>",
        f"📈 معدل النجاح: <b>{success}</b>",
        f"⏱️ متوسط الاستخراج: <b>{latency}</b>",
        "",
        f"🧩 سياسة الإنتاج: <code>{html.escape(str(data['production_policy']))}</code>",
        f"🧪 سياسات مرشحة: <b>{data['policy_candidates']}</b>  |  تقييمات: <b>{data['evaluations']}</b>",
        "",
        "🌐 <b>أكثر المصادر المسجلة</b>",
    ]
    if data["hosts"]:
        for row in data["hosts"]:
            lines.append(f"• {html.escape(str(row['host']))}: {int(row['total'])}")
    else:
        lines.append("• لا توجد بيانات بعد.")

    lines += ["", "🧭 <b>أنماط الاختيار المسجلة</b>"]
    if data["kinds"]:
        for row in data["kinds"]:
            total = int(row["total"] or 0)
            ok = int(row["successes"] or 0)
            rate = round(ok * 100 / total, 1) if total else 0
            lines.append(f"• {html.escape(str(row['kind']))}: {total} — نجاح {rate}%")
    else:
        lines.append("• لا توجد نتائج مرتبطة حتى الآن.")

    lines += ["", "🚨 <b>أكثر أسباب الفشل المسجلة</b>"]
    if data["failures_by_reason"]:
        for row in data["failures_by_reason"]:
            lines.append(f"• {html.escape(str(row['reason']))}: {int(row['total'])}")
    else:
        lines.append("• لا توجد أسباب فشل مرتبطة.")

    lines += ["", "🔎 <b>أكثر إشارات التشخيص</b>"]
    if data["diagnostics"]:
        for name, count in data["diagnostics"]:
            lines.append(f"• {html.escape(name)}: {count}")
    else:
        lines.append("• لا توجد إشارات تشخيص مسجلة.")

    lines += [
        "",
        "⚠️ <b>حدود القياس الحالية</b>",
        "سجل Smart الحالي لا يحفظ سلسلة fallback التشغيلية كاملة (المسار الأولي ← البدائل ← الإنقاذ). لذلك لا يتم اختلاق Rescue Rate أو نسبة استخدام fallback.",
        "",
        "🔐 القراءة فقط: لا يتم تعديل التنزيلات أو سياسات الإنتاج أو بيانات المستخدمين.",
    ]
    return "\n".join(lines)[:_MAX_TEXT]


async def fallback_intelligence_callback(update: Update, context, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    try:
        data = collect_fallback_intelligence()
        await query.edit_message_text(_render(data), parse_mode="HTML", reply_markup=_keyboard(), disable_web_page_preview=True)
    except Exception:
        await query.edit_message_text(
            "🧠 <b>Fallback Intelligence</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🔴 تعذر قراءة Smart telemetry حاليًا.\n\nℹ️ لم يتم تعديل بيانات النظام.",
            parse_mode="HTML",
            reply_markup=_keyboard(),
        )
    raise ApplicationHandlerStop


def register_admin_fallback_intelligence(app: Any, owner_id: int) -> None:
    """Register the read-only fallback intelligence route exactly once."""
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: fallback_intelligence_callback(u, c, owner_id),
            pattern=rf"^{_CALLBACK}$",
        ),
        group=-150,
    )
