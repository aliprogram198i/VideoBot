"""Canonical admin monitoring center for AliBot.

Owns three admin capabilities:
- Error & Incident Center
- Platform / Resolver Monitor
- Admin Alerts

The module is read-heavy and fail-closed. It consumes the existing error_logs
telemetry and stores only small administrative alert state in the existing
SQLite database. It never changes downloader behavior or user records.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from .admin_common import authorize

_MAX_TEXT = 3900
_HOME = "admin_monitoring"
_INCIDENTS = "admin_incidents"
_RESOLVERS = "admin_resolver_monitor"
_ALERTS = "admin_alerts"
_LAST_ERROR = "admin_last_error"
_ACK_PREFIX = "admin_alert_ack_"
_RESOLVE_PREFIX = "admin_alert_resolve_"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cutoff(hours: int = 24) -> str:
    return (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")


def _authorized(update: Update, get_db, owner_id: int, permission: str = "monitoring.view") -> bool:
    return authorize(update, get_db, owner_id, permission)


def _audit(get_db, admin_id: int, action: str, target_id: int | None = None) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO admin_audit_logs "
            "(admin_id, action, target_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (admin_id, action, target_id, None, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def ensure_schema(get_db) -> None:
    if not callable(get_db):
        return
    conn = get_db()
    if conn is None:
        return
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                platform TEXT,
                resolver TEXT,
                event_count INTEGER NOT NULL DEFAULT 1,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                acknowledged_by INTEGER,
                acknowledged_at TEXT,
                resolved_by INTEGER,
                resolved_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_alerts_status_seen "
            "ON admin_alerts(status, last_seen DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_alerts_category_seen "
            "ON admin_alerts(category, last_seen DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _latest_error_row(get_db) -> Any | None:
    """Return the newest raw error telemetry row for the admin diagnostic snapshot."""
    conn = get_db()
    try:
        tables = {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "error_logs" not in tables:
            return None
        columns = _table_columns(conn, "error_logs")
        if "created_at" not in columns:
            return None
        return conn.execute(
            "SELECT * FROM error_logs ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()


_SENSITIVE_KEY_RE = re.compile(
    r"(?:token|password|passwd|secret|api[_-]?key|authorization|cookie|session|access[_-]?token|refresh[_-]?token)",
    re.I,
)
_SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:token|password|passwd|secret|api[_-]?key|authorization|cookie|session|access[_-]?token|refresh[_-]?token)=)[^&#\s]+",
    re.I,
)


def _sanitize_diagnostic_value(value: Any, key: str = "") -> Any:
    """Keep diagnostics useful while preventing credentials from entering a copyable snapshot."""
    if _SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize_diagnostic_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_diagnostic_value(item, key) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_QUERY_RE.sub(r"\1[REDACTED]", value)
    return value


def _diagnostic_phase(details: Any, row: Any) -> str:
    """Derive a concise phase label without inventing telemetry."""
    if isinstance(details, dict):
        phase_values = _find_values(
            details,
            {"phase", "pipeline_phase", "current_phase", "operation", "operation_stage"},
        )
        for value in phase_values:
            value = value.strip()
            if value:
                return value[:120]
    stage = str(row["stage"] or "").strip() if "stage" in row.keys() else ""
    return stage[:120] or "unknown"


def _diagnostic_summary(row: Any, details: Any) -> dict[str, Any]:
    """Build deterministic facts from the existing error row and details_json."""
    summary: dict[str, Any] = {
        "record_source": "error_logs",
        "terminal_error": str(row["error_type"] or "") if "error_type" in row.keys() else "",
        "phase": _diagnostic_phase(details, row),
    }
    if "attempt_id" in row.keys():
        summary["attempt_id"] = row["attempt_id"]
    if "attempt_number" in row.keys():
        summary["attempt_number"] = row["attempt_number"]
    if "created_at" in row.keys():
        summary["created_at"] = row["created_at"]
    if "url" in row.keys():
        summary["url"] = row["url"]
    return _sanitize_diagnostic_value(summary)


def _attempt_error_rows(get_db, attempt_id: str) -> list[Any]:
    """Return all error telemetry rows belonging to the same download attempt."""
    if not attempt_id:
        return []
    conn = get_db()
    try:
        tables = {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "error_logs" not in tables:
            return []
        columns = _table_columns(conn, "error_logs")
        if "attempt_id" not in columns:
            return []
        selected = [name for name in (
            "id", "website", "media_type", "stage", "error_type",
            "error_message", "attempt_id", "attempt_number", "http_status",
            "details_json", "created_at", "duration_ms", "return_code",
            "exception_type", "response_type", "bytes_downloaded",
            "candidate_index", "candidate_count", "yoinku_used",
        ) if name in columns]
        order = "created_at ASC"
        if "id" in columns:
            order += ", id ASC"
        return conn.execute(
            f"SELECT {', '.join(selected)} FROM error_logs "
            "WHERE attempt_id = ? ORDER BY " + order,
            (attempt_id,),
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()


def _render_attempt_timeline(rows: list[Any]) -> list[dict[str, Any]]:
    """Render correlated attempt evidence without changing downloader telemetry."""
    events: list[dict[str, Any]] = []
    for row in rows:
        details_raw = row["details_json"] if "details_json" in row.keys() else None
        details = _json(details_raw)
        event = {
            key: _sanitize_diagnostic_value(row[key], key)
            for key in row.keys()
            if key != "details_json"
        }
        if details is not None:
            event["details"] = _sanitize_diagnostic_value(details, "details_json")
        events.append(event)
    return events


def _render_last_error(get_db) -> list[str]:
    """Build a bounded, copy-friendly diagnostic snapshot from existing telemetry only."""
    row = _latest_error_row(get_db)
    if row is None:
        return [
            "🧾 <b>آخر خطأ بالتفصيل</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🟢 لا يوجد أي خطأ مسجل في <code>error_logs</code>."
        ]

    details_raw = row["details_json"] if "details_json" in row.keys() else None
    details = _json(details_raw)
    sanitized_details = _sanitize_diagnostic_value(
        details if details is not None else details_raw,
        "details_json",
    )

    data: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key == "details_json":
            data[key] = sanitized_details
        else:
            data[key] = _sanitize_diagnostic_value(value, key)

    attempt_id = str(row["attempt_id"] or "").strip() if "attempt_id" in row.keys() else ""
    correlated_rows = _attempt_error_rows(get_db, attempt_id)
    timeline = _render_attempt_timeline(correlated_rows)

    lines = [
        "🧾 <b>آخر خطأ بالتفصيل — Diagnostic Snapshot</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "انسخ <b>كل الأجزاء</b> من هذه الرسالة وأرسلها للتحليل.",
        "<b>مصدر البيانات:</b> telemetry الحالية فقط؛ لا يتم اختراع معلومات غير مسجلة.",
        "<b>الأمان:</b> تم إخفاء credentials وcookies وtokens وsession values وsigned query values.",
        "",
        "<b>=== INCIDENT SUMMARY ===</b>",
        json.dumps(_diagnostic_summary(row, details), ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "<b>=== ERROR_LOG ROW ===</b>",
    ]
    if attempt_id:
        lines.extend([
            "",
            "<b>=== ATTEMPT TIMELINE / CORRELATED EVIDENCE ===</b>",
            f"events_in_same_attempt: {len(timeline)}",
            json.dumps(timeline, ensure_ascii=False, indent=2, sort_keys=True),
        ])

    for key, value in data.items():
        if key == "details_json" and isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            rendered = "null" if value is None else str(value)
        lines.append(f"{key}: {rendered}")

    raw = "\n".join(lines)
    chunks: list[str] = []
    current = ""
    for line in raw.splitlines(True):
        if len(current) + len(line) > 3600 and current:
            chunks.append(current.rstrip())
            current = ""
        current += line
    if current:
        chunks.append(current.rstrip())
    return [f"<pre>{html.escape(chunk)}</pre>" for chunk in chunks] or ["<pre>لا توجد تفاصيل.</pre>"]


def _error_rows(get_db, hours: int = 24, limit: int = 1000) -> list[Any]:
    conn = get_db()
    try:
        tables = {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "error_logs" not in tables:
            return []

        columns = _table_columns(conn, "error_logs")
        wanted = [
            "id", "user_id", "url", "website", "media_type", "stage", "error_type",
            "error_message", "attempt_id", "attempt_number", "http_status",
            "details_json", "created_at",
        ]
        selected = [name for name in wanted if name in columns]
        if "created_at" not in columns:
            return []
        sql = (
            f"SELECT {', '.join(selected)} FROM error_logs "
            "WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?"
        )
        return conn.execute(sql, (_cutoff(hours), int(limit))).fetchall()
    except Exception:
        return []
    finally:
        conn.close()


def _json(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _find_values(value: Any, keys: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys and item not in (None, ""):
                found.append(str(item))
            found.extend(_find_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_values(item, keys))
    return found


def _resolver(row: Any) -> str:
    details = _json(row["details_json"]) if "details_json" in row.keys() else None
    values = _find_values(
        details,
        {"resolver", "resolver_name", "selected_resolver", "fallback_resolver"},
    )
    for value in values:
        value = value.strip()
        if value:
            return value[:80]
    stage = str(row["stage"] or "unknown").strip() if "stage" in row.keys() else "unknown"
    return stage[:80] or "unknown"


def _platform(row: Any) -> str:
    website = str(row["website"] or "").strip() if "website" in row.keys() else ""
    if website:
        return website[:80]
    raw = str(row["url"] or "") if "url" in row.keys() else ""
    match = re.match(r"^[a-z][a-z0-9+.-]*://([^/]+)", raw, re.I)
    return (match.group(1) if match else "unknown")[:80]


def _message(row: Any) -> str:
    value = str(row["error_message"] or "").strip() if "error_message" in row.keys() else ""
    value = re.sub(r"https?://\S+", "<url>", value)
    value = re.sub(r"\b\d{6,}\b", "<id>", value)
    value = re.sub(r"\s+", " ", value)
    return value[:320] or "Unknown error"


def _severity(error_type: str, count: int) -> str:
    if error_type == "all_methods_failed":
        return "critical"
    if error_type in {"database_error", "runtime_error", "startup_error"}:
        return "high"
    if count >= 5:
        return "high"
    if count >= 2:
        return "medium"
    return "low"


def _fingerprint(platform: str, resolver: str, error_type: str, message: str) -> str:
    normalized = re.sub(r"[^a-z0-9:_-]+", " ", message.lower()).strip()
    payload = f"{platform}|{resolver}|{error_type}|{normalized[:220]}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _refresh_alerts(get_db) -> int:
    """Refresh alerts from terminal download outcomes, not intermediate failures.

    A single attempt can emit several error_logs rows while the fallback chain
    is still running. Those rows remain valuable telemetry, but they are not
    independent admin incidents. An alert is therefore created only when the
    attempt reaches the terminal ``all_methods_failed`` outcome.
    """
    rows = _error_rows(get_db, 24, 1000)
    if not rows:
        return 0

    attempts: dict[str, list[Any]] = {}
    legacy_terminal_rows: list[Any] = []

    for row in rows:
        attempt_id = (
            str(row["attempt_id"] or "").strip()
            if "attempt_id" in row.keys()
            else ""
        )
        if attempt_id:
            attempts.setdefault(attempt_id, []).append(row)
        else:
            error_type = (
                str(row["error_type"] or "UnknownError")
                if "error_type" in row.keys()
                else "UnknownError"
            )
            if error_type == "all_methods_failed":
                legacy_terminal_rows.append(row)

    grouped: dict[str, dict[str, Any]] = {}

    def add_terminal(row: Any, failed_stages: list[str] | None = None) -> None:
        platform = _platform(row)
        resolver = "download"
        error_type = "all_methods_failed"
        message = _message(row)
        if failed_stages:
            unique_stages = list(dict.fromkeys(failed_stages))
            details = (
                f"{message} "
                "Stages failed before terminal outcome: " + ", ".join(unique_stages[:8]) + "."
            )
        else:
            details = message

        fp = _fingerprint(platform, resolver, error_type, message)
        item = grouped.setdefault(
            fp,
            {
                "platform": platform,
                "resolver": resolver,
                "error_type": error_type,
                "message": details,
                "count": 0,
                "first_seen": str(row["created_at"] or _now()),
                "last_seen": str(row["created_at"] or _now()),
            },
        )
        item["count"] += 1
        item["first_seen"] = min(
            item["first_seen"],
            str(row["created_at"] or item["first_seen"]),
        )
        item["last_seen"] = max(
            item["last_seen"],
            str(row["created_at"] or item["last_seen"]),
        )

    for rows_for_attempt in attempts.values():
        terminal_rows = [
            row
            for row in rows_for_attempt
            if (
                str(row["error_type"] or "UnknownError")
                if "error_type" in row.keys()
                else "UnknownError"
            ) == "all_methods_failed"
        ]
        if not terminal_rows:
            continue

        terminal_row = max(
            terminal_rows,
            key=lambda row: str(row["created_at"] or ""),
        )
        failed_stages = [
            str(row["stage"] or "").strip()
            for row in rows_for_attempt
            if "stage" in row.keys() and str(row["stage"] or "").strip()
        ]
        add_terminal(terminal_row, failed_stages)

    for row in legacy_terminal_rows:
        add_terminal(row)

    conn = get_db()
    changed = 0
    try:
        conn.execute(
            """
            UPDATE admin_alerts
            SET status='resolved',
                resolved_at=COALESCE(resolved_at, ?)
            WHERE status IN ('open', 'acknowledged')
              AND title NOT LIKE '% — all_methods_failed'
            """,
            (_now(),),
        )

        for fp, item in grouped.items():
            severity = _severity(item["error_type"], item["count"])
            title = f"{item['platform']} / {item['resolver']} — {item['error_type']}"
            existing = conn.execute(
                "SELECT id, status FROM admin_alerts WHERE fingerprint = ?", (fp,)
            ).fetchone()
            if existing and existing["status"] == "resolved":
                continue
            conn.execute(
                """
                INSERT INTO admin_alerts
                    (fingerprint, category, severity, status, title, details,
                     platform, resolver, event_count, first_seen, last_seen)
                VALUES (?, 'incident', ?, 'open', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    severity=excluded.severity,
                    title=excluded.title,
                    details=excluded.details,
                    platform=excluded.platform,
                    resolver=excluded.resolver,
                    event_count=excluded.event_count,
                    first_seen=excluded.first_seen,
                    last_seen=excluded.last_seen
                """,
                (
                    fp, severity, title, item["message"], item["platform"], item["resolver"],
                    item["count"], item["first_seen"], item["last_seen"],
                ),
            )
            changed += 1
        conn.commit()
    finally:
        conn.close()
    return changed

def _alert_rows(get_db, status: str | None = "open", limit: int = 12) -> list[Any]:
    conn = get_db()
    try:
        if status:
            return conn.execute(
                "SELECT * FROM admin_alerts WHERE status = ? ORDER BY "
                "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'medium' THEN 2 ELSE 3 END, last_seen DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM admin_alerts ORDER BY last_seen DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()


def _counts(get_db) -> dict[str, int]:
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT
                 SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_count,
                 SUM(CASE WHEN status='open' AND severity='critical' THEN 1 ELSE 0 END) AS critical,
                 SUM(CASE WHEN status='open' AND severity='high' THEN 1 ELSE 0 END) AS high,
                 SUM(CASE WHEN status='acknowledged' THEN 1 ELSE 0 END) AS acknowledged
               FROM admin_alerts"""
        ).fetchone()
        return {k: int(row[k] or 0) for k in ("open_count", "critical", "high", "acknowledged")}
    finally:
        conn.close()


def _severity_icon(value: str) -> str:
    return {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(value, "⚪")


def _home_keyboard() -> InlineKeyboardMarkup:
    """Compact navigation for the canonical monitoring center."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚨 الحوادث والتنبيهات", callback_data=_ALERTS)],
        [InlineKeyboardButton("📋 السجلات / المنصات", callback_data=_RESOLVERS)],
        [InlineKeyboardButton("📡 المراقبة الذكية", callback_data="admin_observability")],
        [InlineKeyboardButton("🔄 تحديث", callback_data=_HOME),
         InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


def _incident_keyboard() -> InlineKeyboardMarkup:
    """Compatibility keyboard for legacy incident callbacks."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 السجلات / المنصات", callback_data=_RESOLVERS)],
        [InlineKeyboardButton("🔄 تحديث", callback_data=_ALERTS),
         InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


def _resolver_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚨 الحوادث والتنبيهات", callback_data=_ALERTS)],
        [InlineKeyboardButton("🔄 تحديث", callback_data=_RESOLVERS),
         InlineKeyboardButton("📡 المراقبة الذكية", callback_data="admin_observability")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


def _alerts_keyboard(rows: list[Any]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🧾 آخر خطأ بالتفصيل", callback_data=_LAST_ERROR)],
    ]
    for row in rows[:8]:
        alert_id = int(row["id"])
        if row["status"] == "open":
            buttons.append([
                InlineKeyboardButton(f"✓ إقرار #{alert_id}", callback_data=f"{_ACK_PREFIX}{alert_id}"),
                InlineKeyboardButton(f"حل #{alert_id}", callback_data=f"{_RESOLVE_PREFIX}{alert_id}"),
            ])
    buttons += [
        [InlineKeyboardButton("📋 السجلات / المنصات", callback_data=_RESOLVERS)],
        [InlineKeyboardButton("🔄 تحديث", callback_data=_ALERTS),
         InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ]
    return InlineKeyboardMarkup(buttons)


def _last_error_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 تحديث آخر خطأ", callback_data=_LAST_ERROR)],
        [InlineKeyboardButton("🚨 الحوادث والتنبيهات", callback_data=_ALERTS)],
        [InlineKeyboardButton("📋 السجلات / المنصات", callback_data=_RESOLVERS),
         InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


def _render_home(get_db) -> str:
    _refresh_alerts(get_db)
    counts = _counts(get_db)
    return (
        "📡 <b>مركز المراقبة الإدارية</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🚨 حوادث مفتوحة: <b>{counts['open_count']}</b>\n"
        f"🔴 حرجة: <b>{counts['critical']}</b>\n"
        f"🟠 عالية: <b>{counts['high']}</b>\n"
        f"🔔 مُقرّ بها: <b>{counts['acknowledged']}</b>\n\n"
        "المركز الموحد يربط الحوادث، مراقبة المنصات/Resolvers، والتنبيهات في طبقة إدارية واحدة."
    )


def _render_incidents(get_db) -> str:
    _refresh_alerts(get_db)
    rows = _alert_rows(get_db, "open", 10)
    lines = ["🚨 <b>Error & Incident Center</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not rows:
        lines.append("🟢 لا توجد حوادث مفتوحة من سجل الأخطاء خلال آخر 24 ساعة.")
    else:
        for row in rows:
            lines += [
                f"{_severity_icon(row['severity'])} <b>#{int(row['id'])} {html.escape(str(row['title']))}</b>",
                f"   📌 {int(row['event_count'])} حدث • آخر ظهور: {html.escape(str(row['last_seen']))}",
                f"   📝 {html.escape(str(row['details'] or ''))[:260]}",
                "",
            ]
    return "\n".join(lines)[:_MAX_TEXT]


def _render_resolvers(get_db) -> str:
    rows = _error_rows(get_db, 24, 1000)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        platform = _platform(row)
        resolver = _resolver(row)
        key = (platform, resolver)
        item = groups.setdefault(key, {"events": 0, "attempts": set(), "last_seen": ""})
        item["events"] += 1
        attempt = str(row["attempt_id"] or "") if "attempt_id" in row.keys() else ""
        if attempt:
            item["attempts"].add(attempt)
        seen = str(row["created_at"] or "")
        item["last_seen"] = max(item["last_seen"], seen)

    lines = [
        "🌐 <b>Platform / Resolver Monitor</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "المصدر: error telemetry لآخر 24 ساعة. عدد المحاولات هنا هو عدد attempt_id الفريدة التي سجلت خطأ؛ لا يُعرض كـ success rate لأن السجل الحالي لا يسجل نجاح كل Resolver على حدة.",
        "",
    ]
    if not groups:
        lines.append("🟢 لا توجد أخطاء Resolver مسجلة خلال آخر 24 ساعة.")
    else:
        for (platform, resolver), item in sorted(groups.items(), key=lambda x: (-x[1]["events"], x[0])):
            lines.append(
                f"• <b>{html.escape(platform)}</b> / <code>{html.escape(resolver)}</code>"
                f" — أخطاء: <b>{item['events']}</b>"
                f" — محاولات متأثرة: <b>{len(item['attempts'])}</b>"
                f" — آخر: {html.escape(item['last_seen'])}"
            )
    return "\n".join(lines)[:_MAX_TEXT]


def _render_alerts(get_db) -> tuple[str, InlineKeyboardMarkup]:
    _refresh_alerts(get_db)
    rows = _alert_rows(get_db, None, 12)
    counts = _counts(get_db)
    lines = [
        "🔔 <b>Admin Alerts</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🔴 حرجة مفتوحة: <b>{counts['critical']}</b>  |  🟠 عالية: <b>{counts['high']}</b>",
        f"🚨 مفتوحة: <b>{counts['open_count']}</b>  |  ✓ مُقرّ بها: <b>{counts['acknowledged']}</b>",
        "",
    ]
    if not rows:
        lines.append("🟢 لا توجد تنبيهات مسجلة.")
    else:
        for row in rows:
            status = {"open": "مفتوح", "acknowledged": "مُقرّ به", "resolved": "محلول"}.get(row["status"], row["status"])
            lines.append(
                f"{_severity_icon(row['severity'])} <b>#{int(row['id'])}</b> {html.escape(str(row['title']))}"
                f" — {html.escape(status)} — {int(row['event_count'])} حدث"
            )
    return "\n".join(lines)[:_MAX_TEXT], _alerts_keyboard(rows)


async def monitoring_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    await query.edit_message_text(_render_home(get_db), parse_mode="HTML", reply_markup=_home_keyboard())
    raise ApplicationHandlerStop


async def incidents_callback(update: Update, context, get_db, owner_id: int) -> None:
    """Legacy incident callback routed to the canonical alerts view."""
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _audit(get_db, owner_id, "view_error_incident_center")
    text, keyboard = _render_alerts(get_db)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    raise ApplicationHandlerStop


async def resolver_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _audit(get_db, owner_id, "view_platform_resolver_monitor")
    await query.edit_message_text(_render_resolvers(get_db), parse_mode="HTML", reply_markup=_resolver_keyboard())
    raise ApplicationHandlerStop


async def alerts_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _audit(get_db, owner_id, "view_admin_alerts")
    text, keyboard = _render_alerts(get_db)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    raise ApplicationHandlerStop


async def last_error_callback(update: Update, context, get_db, owner_id: int) -> None:
    """Show a copy-friendly snapshot of the newest raw error telemetry."""
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _audit(get_db, owner_id, "view_last_error_diagnostic")
    chunks = _render_last_error(get_db)
    await query.edit_message_text(
        chunks[0],
        parse_mode="HTML",
        reply_markup=_last_error_keyboard(),
    )
    for chunk in chunks[1:]:
        await query.message.reply_text(chunk, parse_mode="HTML")
    raise ApplicationHandlerStop


async def alert_action_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id, "monitoring.manage"):
        return
    data = str(query.data or "")
    action = "acknowledge" if data.startswith(_ACK_PREFIX) else "resolve"
    prefix = _ACK_PREFIX if action == "acknowledge" else _RESOLVE_PREFIX
    try:
        alert_id = int(data[len(prefix):])
    except (TypeError, ValueError):
        return

    conn = get_db()
    try:
        if action == "acknowledge":
            conn.execute(
                "UPDATE admin_alerts SET status='acknowledged', acknowledged_by=?, acknowledged_at=? "
                "WHERE id=? AND status='open'",
                (owner_id, _now(), alert_id),
            )
        else:
            conn.execute(
                "UPDATE admin_alerts SET status='resolved', resolved_by=?, resolved_at=? "
                "WHERE id=? AND status IN ('open','acknowledged')",
                (owner_id, _now(), alert_id),
            )
        conn.commit()
    finally:
        conn.close()

    _audit(get_db, owner_id, f"admin_alert_{action}", target_id=alert_id)
    text, keyboard = _render_alerts(get_db)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    raise ApplicationHandlerStop


def register_admin_monitoring(app: Any, get_db, owner_id: int) -> None:
    """Install the single owner for the three monitoring capabilities."""
    ensure_schema(get_db)
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: monitoring_callback(u, c, get_db, owner_id),
            pattern=rf"^{_HOME}$",
        ),
        group=-210,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: incidents_callback(u, c, get_db, owner_id),
            pattern=rf"^(?:{_INCIDENTS}|admin_ops_incidents)$",
        ),
        group=-210,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: resolver_callback(u, c, get_db, owner_id),
            pattern=rf"^(?:{_RESOLVERS}|admin_ops_platforms|admin_ops_resolvers)$",
        ),
        group=-210,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: alerts_callback(u, c, get_db, owner_id),
            pattern=rf"^{_ALERTS}$",
        ),
        group=-210,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: last_error_callback(u, c, get_db, owner_id),
            pattern=rf"^{_LAST_ERROR}$",
        ),
        group=-210,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: alert_action_callback(u, c, get_db, owner_id),
            pattern=rf"^{_ACK_PREFIX}[0-9]+$|^{_RESOLVE_PREFIX}[0-9]+$",
        ),
        group=-210,
    )
