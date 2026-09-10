"""Read-only Smart Analytics and anomaly detection for AliBot administration."""

from __future__ import annotations

import html
import os
import sqlite3
from datetime import datetime
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


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except (OSError, sqlite3.Error):
        return None


def _period(conn: sqlite3.Connection, start_hours: int, end_hours: int) -> dict[str, Any]:
    start = f"-{start_hours} hours"
    end = f"-{end_hours} hours"
    outcomes = conn.execute(
        "SELECT success FROM smart_outcomes WHERE julianday(created_at) >= julianday('now', ?) AND julianday(created_at) < julianday('now', ?)",
        (start, end),
    ).fetchall()
    latencies = [float(row[0]) for row in conn.execute(
        "SELECT extraction_ms FROM smart_telemetry WHERE julianday(created_at) >= julianday('now', ?) AND julianday(created_at) < julianday('now', ?) AND extraction_ms IS NOT NULL",
        (start, end),
    ).fetchall() if row[0] is not None]
    telemetry = conn.execute(
        "SELECT COUNT(*) FROM smart_telemetry WHERE julianday(created_at) >= julianday('now', ?) AND julianday(created_at) < julianday('now', ?)",
        (start, end),
    ).fetchone()[0]
    total = len(outcomes)
    success = sum(int(row[0]) for row in outcomes)
    return {
        "telemetry": int(telemetry),
        "outcomes": total,
        "successes": success,
        "failures": total - success,
        "success_rate": round(success * 100 / total, 1) if total else None,
        "avg_ms": round(mean(latencies), 1) if latencies else None,
        "p95_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1) if latencies else None,
    }


def _empty() -> dict[str, Any]:
    return {"available": False, "current": {}, "previous": {}, "hosts": [], "anomalies": [], "recommendations": [], "policy": "غير متاح", "candidates": 0, "evaluations": 0}


def _confidence(sample: int) -> str:
    return "عالٍ" if sample >= 50 else "متوسط" if sample >= 20 else "منخفض"


def collect_smart_analytics(path: Path | None = None) -> dict[str, Any]:
    """Analyze existing telemetry in SQLite read-only mode; never mutate it."""
    conn = _connect_ro(path or _telemetry_path())
    if conn is None:
        return _empty()
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required = {"smart_telemetry", "smart_outcomes", "smart_policy_versions", "smart_evaluations"}
        if not required.issubset(tables):
            return _empty()
        current, previous = _period(conn, 24, 0), _period(conn, 48, 24)
        anomalies: list[dict[str, str]] = []
        recommendations: list[str] = []
        if current["outcomes"] >= 10 and previous["outcomes"] >= 10:
            drop = float(previous["success_rate"] or 0) - float(current["success_rate"] or 0)
            if drop >= 15:
                anomalies.append({"severity": "HIGH" if drop >= 25 else "MEDIUM", "title": "انخفاض معدل النجاح", "detail": f"انخفض {drop:.1f} نقطة مئوية خلال آخر 24 ساعة.", "confidence": _confidence(current["outcomes"])})
                recommendations.append("افحص المنصات ذات أعلى معدل فشل قبل تغيير أي extractor أو policy.")
            if current["avg_ms"] and previous["avg_ms"] and current["avg_ms"] >= previous["avg_ms"] * 1.5:
                increase = (current["avg_ms"] / previous["avg_ms"] - 1) * 100
                anomalies.append({"severity": "MEDIUM", "title": "ارتفاع زمن الاستخراج", "detail": f"ارتفع المتوسط {increase:.1f}%.", "confidence": _confidence(current["outcomes"])})
                recommendations.append("راجع مصادر التأخير وراقب P95 قبل تعديل مسار الاستخراج.")
        if current["telemetry"] >= 20 and previous["telemetry"] >= 10 and current["telemetry"] >= previous["telemetry"] * 2:
            anomalies.append({"severity": "LOW", "title": "ارتفاع حجم الطلبات", "detail": f"{current['telemetry']} مقابل {previous['telemetry']} سابقًا.", "confidence": _confidence(current["telemetry"])})
            recommendations.append("راقب الموارد ومعدل الأخطاء؛ لا توجد تغييرات تلقائية.")

        rows = conn.execute(
            """SELECT COALESCE(t.source_host,'unknown') host, COUNT(o.id) outcomes,
                      SUM(CASE WHEN o.success=0 THEN 1 ELSE 0 END) failures,
                      AVG(t.extraction_ms) avg_ms
               FROM smart_telemetry t LEFT JOIN smart_outcomes o ON o.telemetry_id=t.id
               WHERE julianday(t.created_at) >= julianday('now','-24 hours')
               GROUP BY t.source_host HAVING COUNT(o.id) >= 5
               ORDER BY failures DESC, outcomes DESC LIMIT 8"""
        ).fetchall()
        hosts = []
        for row in rows:
            total, failures = int(row["outcomes"] or 0), int(row["failures"] or 0)
            failure_rate = round(failures * 100 / total, 1) if total else 0
            hosts.append({"host": row["host"], "outcomes": total, "failure_rate": failure_rate, "avg_ms": round(float(row["avg_ms"]), 1) if row["avg_ms"] is not None else None})
            if total >= 10 and failure_rate >= 50:
                anomalies.append({"severity": "HIGH", "title": f"تدهور منصة: {row['host']}", "detail": f"معدل الفشل {failure_rate}%.", "confidence": _confidence(total)})
                recommendations.append(f"تحقق من {row['host']} أولًا؛ لا يتم تعطيله تلقائيًا.")

        production = conn.execute("SELECT version FROM smart_policy_versions WHERE status='production' ORDER BY created_at DESC LIMIT 1").fetchone()
        return {"available": True, "current": current, "previous": previous, "hosts": hosts, "anomalies": anomalies, "recommendations": list(dict.fromkeys(recommendations))[:5], "policy": str(production[0]) if production else "غير متاح", "candidates": int(conn.execute("SELECT COUNT(*) FROM smart_policy_versions WHERE status='candidate'").fetchone()[0]), "evaluations": int(conn.execute("SELECT COUNT(*) FROM smart_evaluations").fetchone()[0])}
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
    cur, prev = data["current"], data["previous"]
    cur_rate = f"{cur['success_rate']}%" if cur["success_rate"] is not None else "غير متاح"
    prev_rate = f"{prev['success_rate']}%" if prev["success_rate"] is not None else "غير متاح"
    avg = f"{cur['avg_ms']} ms" if cur["avg_ms"] is not None else "غير متاح"
    p95 = f"{cur['p95_ms']} ms" if cur["p95_ms"] is not None else "غير متاح"
    lines = ["🧠 <b>Smart Analytics</b>", "━━━━━━━━━━━━━━━━━━━━", "", "📈 <b>مقارنة آخر 24 ساعة</b>", f"• النتائج: {cur['outcomes']} | نجاح: {cur_rate} | السابق: {prev_rate}", f"• Telemetry: {cur['telemetry']} | المتوسط: {avg} | P95: {p95}", "", f"🧩 سياسة الإنتاج: <code>{html.escape(str(data['policy']))}</code>", f"🧪 مرشحون: {data['candidates']} | تقييمات: {data['evaluations']}", "", "🚨 <b>الإشارات المكتشفة</b>"]
    if data["anomalies"]:
        lines += [f"• [{x['severity']}] {html.escape(x['title'])} — {html.escape(x['detail'])} — ثقة {x['confidence']}" for x in data["anomalies"][:6]]
    else:
        lines.append("• 🟢 لا توجد إشارة شذوذ تستوفي عتبات العينة الحالية.")
    lines += ["", "🌐 <b>صحة المصادر</b>"]
    lines += [f"• {html.escape(str(x['host']))}: فشل {x['failure_rate']}% | متوسط {x['avg_ms'] if x['avg_ms'] is not None else '—'} ms" for x in data["hosts"][:6]] or ["• لا توجد عينة كافية للمصادر."]
    lines += ["", "💡 <b>التوصيات</b>"]
    lines += [f"• {html.escape(x)}" for x in data["recommendations"]] or ["• لا توجد توصيات تشغيلية مطلوبة حاليًا."]
    lines += ["", "🔐 <b>وضع آمن</b>", "التحليل استشاري فقط: لا تدريب تلقائي، لا ترقية policy، لا تغيير extractor، ولا تعديل لبيانات المستخدمين أو التنزيلات."]
    return "\n".join(lines)[:_MAX_TEXT]


async def smart_analytics_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "analytics.view"):
        return
    try:
        await query.edit_message_text(_render(collect_smart_analytics()), parse_mode="HTML", reply_markup=_keyboard(), disable_web_page_preview=True)
    except Exception:
        await query.edit_message_text("🧠 <b>Smart Analytics</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🔴 تعذر تنفيذ التحليل حاليًا.\n\nℹ️ لم يتم تعديل بيانات النظام.", parse_mode="HTML", reply_markup=_keyboard())
    raise ApplicationHandlerStop


def register_admin_smart_analytics(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: smart_analytics_callback(u, c, get_db, owner_id), pattern=rf"^{_CALLBACK}$"), group=-150)
