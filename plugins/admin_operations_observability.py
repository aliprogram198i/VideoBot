"""Unified read-only admin operations drill-downs for AliBot.

This module intentionally reuses existing SQLite telemetry. It creates no new
runtime tables and never mutates download/error data.
"""

from __future__ import annotations

import html
import os
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

_MAX_TEXT = 3900
_LOOKBACK_HOURS = 24


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _cutoff() -> str:
    return (datetime.now() - timedelta(hours=_LOOKBACK_HOURS)).isoformat()


def _tables(conn) -> set[str]:
    return {str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def _columns(conn, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _error_rows(get_db, limit: int = 250) -> list[Any]:
    conn = get_db()
    try:
        if "error_logs" not in _tables(conn):
            return []
        columns = _columns(conn, "error_logs")
        fields = [
            "id", "user_id", "url", "website", "media_type", "stage",
            "error_type", "error_message", "attempt_id", "attempt_number",
            "http_status", "details_json", "created_at",
        ]
        selected = [field for field in fields if field in columns]
        if "created_at" not in columns:
            return []
        return conn.execute(
            f"SELECT {', '.join(selected)} FROM error_logs "
            "WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (_cutoff(), int(limit)),
        ).fetchall()
    finally:
        conn.close()


def _value(row: Any, key: str, default: str = "") -> str:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return str(value if value is not None else default)


def _attempt_id(row: Any) -> str:
    return _value(row, "attempt_id").strip()


def _platform(row: Any) -> str:
    website = _value(row, "website").strip()
    if website:
        return website[:80]
    url = _value(row, "url")
    if "://" in url:
        return url.split("://", 1)[1].split("/", 1)[0][:80]
    return "unknown"


def _resolver(row: Any) -> str:
    # Prefer the resolver field already emitted by the existing details_json.
    import json
    try:
        details = json.loads(_value(row, "details_json")) if _value(row, "details_json") else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        details = {}

    def walk(value: Any):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in {
                    "resolver", "resolver_name", "selected_resolver", "fallback_resolver"
                } and item not in (None, ""):
                    yield str(item)
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    for candidate in walk(details):
        candidate = candidate.strip()
        if candidate:
            return candidate[:100]
    return _value(row, "stage", "unknown")[:100] or "unknown"


def _groups(get_db) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _error_rows(get_db):
        key = (_platform(row), _resolver(row))
        item = groups.setdefault(key, {
            "platform": key[0],
            "resolver": key[1],
            "events": 0,
            "attempts": set(),
            "last_seen": "",
        })
        item["events"] += 1
        attempt = _attempt_id(row)
        if attempt:
            item["attempts"].add(attempt)
        item["last_seen"] = max(item["last_seen"], _value(row, "created_at"))
    result = list(groups.values())
    result.sort(key=lambda item: (-item["events"], item["platform"], item["resolver"]))
    return result


def _timeline_rows(get_db, limit: int = 25) -> list[dict[str, Any]]:
    """Merge successful downloads and error events into one chronological feed."""
    conn = get_db()
    try:
        events: list[dict[str, Any]] = []
        if "downloads" in _tables(conn):
            cols = _columns(conn, "downloads")
            fields = [f for f in (
                "id", "user_id", "website", "media_type", "quality", "title", "created_at"
            ) if f in cols]
            if "created_at" in cols:
                rows = conn.execute(
                    f"SELECT {', '.join(fields)} FROM downloads "
                    "WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
                    (_cutoff(), int(limit)),
                ).fetchall()
                for row in rows:
                    events.append({
                        "kind": "download",
                        "created_at": _value(row, "created_at"),
                        "platform": _value(row, "website", "unknown"),
                        "stage": "delivery_success",
                        "attempt_id": "",
                        "error_type": "",
                        "title": _value(row, "title", "بدون عنوان"),
                        "user_id": _value(row, "user_id", "—"),
                    })
        for row in _error_rows(get_db, limit):
            events.append({
                "kind": "error",
                "created_at": _value(row, "created_at"),
                "platform": _platform(row),
                "stage": _value(row, "stage", "unknown"),
                "attempt_id": _attempt_id(row),
                "error_type": _value(row, "error_type", "error"),
                "title": _value(row, "error_message", "Unknown error")[:180],
                "user_id": _value(row, "user_id", "—"),
            })
        events.sort(key=lambda item: item["created_at"], reverse=True)
        return events[:limit]
    finally:
        conn.close()


def _timeline_text(events: list[dict[str, Any]], scope: str | None = None) -> str:
    lines = [
        "🧭 <b>Operations Timeline</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"آخر {_LOOKBACK_HOURS} ساعة • {len(events)} حدثًا معروضًا",
        "",
    ]
    if scope:
        lines.append(f"🔎 النطاق: <code>{html.escape(scope)}</code>")
        lines.append("")
    if not events:
        lines.append("🟢 لا توجد أحداث مسجلة في الفترة الحالية.")
    else:
        for event in events:
            if event["kind"] == "download":
                icon = "🟢"
                detail = f"Delivery SUCCESS • {event['title']}"
            else:
                icon = "🔴"
                detail = f"{event['error_type']} • {event['title']}"
            attempt = f" • attempt=<code>{html.escape(event['attempt_id'][:48])}</code>" if event["attempt_id"] else ""
            lines.append(
                f"{icon} <code>{html.escape(event['created_at'][-19:])}</code> "
                f"<b>{html.escape(event['platform'])}</b> "
                f"{html.escape(event['stage'])}{attempt}"
            )
            lines.append(f"   {html.escape(detail[:250])}")
    return "\n".join(lines)[:_MAX_TEXT]


def _timeline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Resolver Drill-down", callback_data="admin_ops_resolvers")],
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_ops_timeline"),
         InlineKeyboardButton("📊 مركز العمليات", callback_data="admin_ops_dashboard")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ])


async def timeline_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    await query.edit_message_text(
        _timeline_text(_timeline_rows(get_db)),
        parse_mode="HTML",
        reply_markup=_timeline_keyboard(),
        disable_web_page_preview=True,
    )
    raise ApplicationHandlerStop


def _resolver_text(item: dict[str, Any], index: int) -> str:
    return (
        "🌐 <b>Resolver Drill-down</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"#{index + 1} <b>{html.escape(item['platform'])}</b> / "
        f"<code>{html.escape(item['resolver'])}</code>\n"
        f"❌ أحداث الخطأ: <b>{item['events']}</b>\n"
        f"🎯 محاولات متأثرة: <b>{len(item['attempts'])}</b>\n"
        f"🕒 آخر ظهور: <code>{html.escape(item['last_seen'])}</code>\n\n"
        "ℹ️ لا يتم عرض Success Rate للـResolver هنا لأن error_logs لا يمثل نجاحات Resolver الفردية."
    )


def _resolver_keyboard(index: int, total: int) -> InlineKeyboardMarkup:
    buttons = []
    if index > 0:
        buttons.append(InlineKeyboardButton("◀️ السابق", callback_data=f"admin_ops_resolver_{index - 1}"))
    if index + 1 < total:
        buttons.append(InlineKeyboardButton("التالي ▶️", callback_data=f"admin_ops_resolver_{index + 1}"))
    rows = [buttons] if buttons else []
    rows += [
        [InlineKeyboardButton("🧭 Timeline", callback_data="admin_ops_timeline"),
         InlineKeyboardButton("📊 العمليات", callback_data="admin_ops_dashboard")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
    ]
    return InlineKeyboardMarkup(rows)


async def resolvers_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    items = _groups(get_db)
    if not items:
        text = "🌐 <b>Resolver Drill-down</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🟢 لا توجد أخطاء Resolver خلال آخر 24 ساعة."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧭 Timeline", callback_data="admin_ops_timeline")],
            [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
        ])
    else:
        try:
            index = int(str(query.data).rsplit("_", 1)[1]) if str(query.data).startswith("admin_ops_resolver_") else 0
        except (TypeError, ValueError):
            index = 0
        index = max(0, min(index, len(items) - 1))
        text = _resolver_text(items[index], index)
        keyboard = _resolver_keyboard(index, len(items))
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    raise ApplicationHandlerStop


def _storage_paths() -> list[tuple[str, str]]:
    candidates = [
        ("/app/data", "DB / persistent data"),
        ("/app/tmp", "Temporary media"),
        (os.environ.get("TMPDIR", ""), "TMPDIR"),
    ]
    result = []
    seen = set()
    for path, label in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        result.append((path, label))
    return result


def _storage_data() -> list[dict[str, Any]]:
    data = []
    for path, label in _storage_paths():
        try:
            usage = shutil.disk_usage(path) if os.path.exists(path) else None
            data.append({
                "path": path,
                "label": label,
                "exists": bool(usage),
                "used": usage.used if usage else 0,
                "free": usage.free if usage else 0,
                "total": usage.total if usage else 0,
                "percent": round((usage.used / usage.total) * 100, 1) if usage and usage.total else 0.0,
            })
        except (OSError, ValueError):
            data.append({
                "path": path, "label": label, "exists": False,
                "used": 0, "free": 0, "total": 0, "percent": 0.0,
            })
    return data


def _fmt_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _storage_text(rows: list[dict[str, Any]]) -> str:
    lines = [
        "💾 <b>Storage Monitor</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "قراءة مباشرة لمساحات التخزين المتاحة؛ لا يتم حذف أو تعديل أي ملف من هذه الشاشة.",
        "",
    ]
    if not rows:
        lines.append("لا توجد مسارات قابلة للمراقبة.")
    else:
        for row in rows:
            if not row["exists"]:
                lines.append(f"⚪ <b>{html.escape(row['label'])}</b> — <code>{html.escape(row['path'])}</code> غير موجود")
                continue
            free = _fmt_bytes(row["free"])
            total = _fmt_bytes(row["total"])
            icon = "🔴" if row["percent"] >= 90 else "🟠" if row["percent"] >= 75 else "🟢"
            lines.append(
                f"{icon} <b>{html.escape(row['label'])}</b> — "
                f"<code>{html.escape(row['path'])}</code>\n"
                f"   مستخدم: <b>{row['percent']:.1f}%</b> • متاح: <b>{free}</b> / {total}"
            )
    return "\n".join(lines)[:_MAX_TEXT]


async def storage_callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, owner_id):
        return
    await query.edit_message_text(
        _storage_text(_storage_data()),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تحديث", callback_data="admin_ops_storage")],
            [InlineKeyboardButton("📊 مركز العمليات", callback_data="admin_ops_dashboard"),
             InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_home")],
        ]),
    )
    raise ApplicationHandlerStop


def register_admin_operations_observability(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: timeline_callback(u, c, get_db, owner_id),
            pattern=r"^admin_ops_timeline$",
        ),
        group=-205,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: resolvers_callback(u, c, get_db, owner_id),
            pattern=r"^admin_ops_resolvers$|^admin_ops_resolver_[0-9]+$",
        ),
        group=-205,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: storage_callback(u, c, get_db, owner_id),
            pattern=r"^admin_ops_storage$",
        ),
        group=-205,
    )
