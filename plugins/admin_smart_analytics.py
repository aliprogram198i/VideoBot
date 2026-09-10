"""Read-only Smart Analytics and anomaly detection for AliBot administration.

Phase 4 intentionally observes existing Smart telemetry only. It never creates,
updates, deletes, promotes, or rolls back telemetry, policies, users, downloads,
or production configuration. All findings are advisory and sample-size aware.
"""

from __future__ import annotations

import html
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from .admin_common import authorize

_CALLBACK = "admin_smart_analytics"
_MAX_TEXT = 3900


def _telemetry_path() -> Path:
    configured = os.getenv("SMART_DATA_DIR")
    if configured:
        return Path(configured).expanduser() / "smart_learning.db"
    railway_data = Path("/app/data")
    if railway_data.is_dir():
        return railway_data / "smart" / "smart_learning.db"
    return Path(".smart_data") / "smart_learning.db"


def _read_only_connect(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except (OSError, sqlite3.Error):
        return None


def _period_stats(conn: sqlite3.Connection, start_hours: int, end_hours: int) -> dict[str, Any]:
    params = (f"-{start_hours} hours", f"-{end_hours} hours")
    outcome_rows = conn.execute(
        """SELECT success FROM smart_outcomes
           WHERE created_at >= datetime('now', ?) AND created_at < datetime('now', ?)
           ORDER BY id DESC""",
        params,
    ).fetchall()
    latency_rows = conn.execute(
        """SELECT extraction_ms FROM smart_telemetry
           WHERE created_at >= datetime('now', ?) AND created_at < datetime('now', ?)
           AND extraction_ms IS NOT NULL
           ORDER BY id DESC""",
        params,
    ).fetchall()
    telemetry_count = int(conn.execute(
        """SELECT COUNT(*) FROM smart_telemetry
           WHERE created_at >= datetime('now', ?) AND created_at < datetime('now', ?)""",
        params,
    ).fetchone()[0])
    successes = sum(int(row[0]) for row in outcome_rows)
    outcomes = len(outcome_rows)
    failures = outcomes - successes
    latencies = [float(row[0]) for row in latency_rows if row[0] is not None]
    return {
        "telemetry": telemetry_count,
        "outcomes": outcomes,
        "successes": successes,
        "failures": failures,
        "success_rate": round(successes * 100 / outcomes, 1) if outcomes else None,
        "avg_ms": round(mean(latencies), 1) if latencies else None,
        "p95_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1) if latencies else None,
    }


def _empty() -> dict[str, Any]:
    return {
        "available": False,
        "current": {},
        "previous": {},
        "hosts": [],
        "anomalies": [],
        "recommendations": [],
        "policy": "غير متاح",
        "candidates": 0,
        "evaluations": 0,
    }


def _confidence(sample: int) -> str:
    if sample >= 50:
        return "عالٍ"
    if sample >= 20:
        return "متوسط"
    return "منخفض"


def collect_smart_analytics(path: Path | None = None) -> dict[str, Any]:
    """Collect anomaly signals from Smart telemetry using SQLite read-only mode."""
    conn = _read_only_connect(path or _telemetry_path())
    if conn is None:
        return _empty()
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required = {"smart_telemetry", "smart_outcomes", "smart_policy_versions", "smart_evaluations"}
        if not required.issubset(tables):
            return _empty()

        current = _period_stats(conn, 24, 0)
        previous = _period_stats(conn, 48, 24)
        anomalies: list[dict[str, Any]] = []
        recommendations: list[str] = []

        if current["outcomes"] >= 10 and previous["outcomes"] >= 10:
            drop = float(previous["success_rate"] or 0) - float(current["success_rate"] or 0)
            if drop >= 15.0:
                anomalies.append({"severity": "HIGH" if drop >= 25 else "MEDIUM", "title": "انخفاض معدل النجاح", "detail": f"انخفض {drop:.1f} نقطة مئوية خلال آخر 24 ساعة مقارنة بالـ24 ساعة السابقة.", "confidence": _confidence(current["outcomes"])})
                recommendations.append("افحص المنصات ذات أعلى معدل فشل قبل تغيير أي extractor أو policy.")

        if current["avg_ms"] is not None and previous["avg_ms"] is not None and current["outcomes"] >= 10 and previous["outcomes"] >= 10:
            increase = (current["avg_ms"] / previous["avg_ms"] - 1.0) * 100 if previous["avg_ms"] else 0
            if increase >= 50.0:
                anomalies.append({"severity": "MEDIUM", "title": "ارتفاع زمن الاستخراج", "detail": f"ارتفع المتوسط {increase:.1f}% إلى {current['avg_ms']} ms.", "confidence": _confidence(current["outcomes"])})
                recommendations.append("راجع مصادر التأخير وراقب p95 قبل تعديل مسار الاستخراج.")

        if current["telemetry"] >= 20 and previous["telemetry"] >= 10 and current["telemetry"] >= previous["telemetry"] * 2:
            anomalies.append({"severity": "LOW", "title": "ارتفاع حجم الطلبات", "detail": f"Telemetry الحالية {current['telemetry']} مقابل {previous['telemetry']} في الفترة السابقة.", "confidence": _confidence(current["telemetry"])})
            recommendations.append("راقب استهلاك الموارد ومعدل الأخطاء؛ لا يتم تغيير حدود التشغيل تلقائيًا.")

        host_rows = conn.execute(
            """SELECT COALESCE(t.source_host,'unknown') AS host,
                      COUNT(*) AS telemetry,
                      COUNT(o.id) AS outcomes,
                      SUM(CASE WHEN o.success=0 THEN 1 ELSE 0 END) AS failures,
                      AVG(t.extraction_ms) AS avg_ms
               FROM smart_telemetry t
               LEFT JOIN smart_outcomes o ON o.telemetry_id=t.id
               WHERE t.created_at >= datetime('now','-24 hours')
               GROUP BY t.source_host
               HAVING COUNT(o.id) >= 5
               ORDER BY failures DESC, outcomes DESC
               LIMIT 8"""
        ).fetchall()
        hosts: list[dict[str, Any]] = []
        for row in host_rows:
            outcomes = int(row["outcomes"] or 0)
            failures = int(row["failures"] or 0)
            rate = round((outcomes - failures) * 100 / outcomes, 1) if outcomes else None
            hosts.append({"host": row["host"], "outcomes": outcomes, "failure_rate": round(failures * 100 / outcomes, 1) if outcomes else 0.0, "success_rate": rate, "avg_ms": round(float(row["avg_ms"]), 1) if row["avg_ms"] is not None else None})
            if outcomes >= 10 and failures * 100 / outcomes >= 50:
                anomalies.append({"severity": "HIGH", "title": f"تدهور منصة: {row['host']}", "detail": f"معدل الفشل {failures * 100 / outcomes:.1f}% خلال آخر 24 ساعة.", "confidence": _confidence(outcomes)})
                recommendations.append(f"تحقق من {row['host']} أولًا؛ لا يتم تعطيله أو تغيير policy تلقائيًا.")

        production = conn.execute(
            "SELECT version FROM smart_policy_versions WHERE status='production' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        candidates = int(conn.execute("SELECT COUNT(*) FROM smart_policy_versions WHERE status='candidate'").fetchone()[0])
        evaluations = int(conn.execute("SELECT COUNT(*) FROM smart_evaluations").fetchone()[0])
        return {
            "available": True,
            "current": current,
            "previous": previous,
            "hosts": hosts,
            "anomalies": anomalies,
            "recommendations": list(dict.fromkeys(recommendations))[:5],
            "policy": str(production[0]) if production else "غير متاح",
            "candidates": candidates,
            "evaluations": evaluations,
        }
    except sqlite3.Error:
        return _empty()
    finally:
        conn.close()


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data=_CALLBACK)],
        [InlineKeyboardButton("🤖 Smart Operations", callback_data="admin_smart_operations")],
        [InlineKeyboardButton("🧠 Fallback Intelligence", callback_data="admin_fallback_intelligence")],
        [InlineKeyboardButton("📊 مركز العمليات", callback_data="admin_ops_dashboard")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


def _render(data: dict[str, Any]) -> str:
    if not data["available"]:
        return "🧠 <b>Smart Analytics</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🟠 بيانات التحليل غير متاحة حاليًا.\n\nℹ️ لم يتم إنشاء أو تعديل أي قاعدة بيانات."
    current = data["current"]
    previous = data["previous"]
    cur_rate = f"{current['success_rate']}%" if current["success_rate"] is not None else "غير متاح"
    prev_rate = f"{previous['success_rate']}%" if previous["success_rate"] is not None else "غير متاح"
    cur_lat = f"{current['avg_ms']} ms" if current["avg_ms"] is not None else "غير متاح"
    p95 = f"{current['p95_ms']} ms" if current["p95_ms"] is not None else "غير متاح"
    lines = [
        "🧠 <b>Smart Analytics</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "📈 <b>مقارنة آخر 24 ساعة</b>",
        f"• النتائج: {current['outcomes']} | نجاح: {cur_rate} | السابق: {prev_rate}",
        f"• Telemetry: {current['telemetry']} | المتوسط: {cur_lat} | P95: {p95}",
        "",
        f"🧩 سياسة الإنتاج: <code>{html.escape(str(data['policy']))}</code>",
        f"🧪 مرشحون: {data['candidates']} | تقييمات: {data['evaluations']}",
        "",
        "🚨 <b>الإشارات المكتشفة</b>",
    ]
    if data["anomalies"]:
        for item in data["anomalies"][:6]:
            lines.append(f"• [{item['severity']}] {html.escape(item['title'])} — {html.escape(item['detail'])} — ثقة {item['confidence']}")
    else:
        lines.append("• 🟢 لا توجد إشارة شذوذ تستوفي عتبات العينة الحالية.")

    lines += ["", "🌐 <b>صحة المصادر</b>"]
    if data["hosts"]:
        for host in data["hosts"][:6]:
            lines.append(f"• {html.escape(str(host['host']))}: فشل {host['failure_rate']}% | متوسط {host['avg_ms'] if host['avg_ms'] is not None else '—'} ms")
    else:
        lines.append("• لا توجد عينة كافية للمصادر.")

    lines += ["", "💡 <b>التوصيات</b>"]
    if data["recommendations"]:
        for item in data["recommendations"]:
            lines.append(f"• {html.escape(item)}")
    else:
        lines.append("• لا توجد توصيات تشغيلية مطلوبة حاليًا.")

    lines += ["", "🔐 <b>وضع آمن</b>", "التحليل استشاري فقط: لا تدريب تلقائي، لا ترقية policy، لا تغيير extractor، ولا تعديل لبيانات المستخدمين أو التنزيلات."]
    return "\n".join(lines)[:_MAX_TEXT]


async def smart_analytics_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "analytics.view"):
        return
    try:
        data = collect_smart_analytics()
        await query.edit_message_text(_render(data), parse_mode="HTML", reply_markup=_keyboard(), disable_web_page_preview=True)
    except Exception:
        await query.edit_message_text(
            "🧠 <b>Smart Analytics</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🔴 تعذر تنفيذ التحليل حاليًا.\n\nℹ️ لم يتم تعديل بيانات النظام.",
            parse_mode="HTML", reply_markup=_keyboard(),
        )
    raise ApplicationHandlerStop


def register_admin_smart_analytics(app: Any, get_db, owner_id: int) -> None:
    """Register the analytics route once at the isolated admin group."""
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: smart_analytics_callback(u, c, get_db, owner_id),
            pattern=rf"^{_CALLBACK}$",
        ),
        group=-150,
    )
