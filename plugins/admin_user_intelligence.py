"""Admin user intelligence built from existing users/downloads data."""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from .admin_common import authorize
from .user_location import request_location_to_user

_PREFIX = "admin_user_intel_"


def _authorized(update, get_db, owner_id: int) -> bool:
    return authorize(update, get_db, owner_id, "users.view")


def _safe(value: Any, fallback: str = "غير متوفر") -> str:
    text = str(value).strip() if value is not None else ""
    return html.escape(text or fallback)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _segment(downloads: int, active_days: int, last_seen: Any) -> str:
    last = _parse_dt(last_seen)
    age_days = 9999
    if last:
        age_days = max(0, (datetime.now(timezone.utc) - last).days)
    if age_days > 30:
        return "💤 خامل"
    if downloads >= 50 or active_days >= 20:
        return "🔥 شديد النشاط"
    if downloads >= 20 or active_days >= 10:
        return "🟢 نشط"
    if downloads <= 2 and active_days <= 2:
        return "🆕 جديد"
    return "👤 عادي"


async def intelligence_callback(update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    try:
        user_id = int((query.data or "").rsplit("_", 1)[1])
    except (ValueError, IndexError):
        return
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT user_id, username, first_name, downloads, first_seen, last_seen, is_banned, country, country_code, country_source, location_updated_at FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        rows = conn.execute(
            "SELECT website, media_type, quality, created_at FROM downloads WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    if not user:
        await query.edit_message_text("❌ المستخدم غير موجود.")
        return

    total = len(rows)
    active_days = len({str(r["created_at"])[:10] for r in rows if r["created_at"]})
    websites = Counter(str(r["website"] or "Other") for r in rows)
    media = Counter(str(r["media_type"] or "unknown") for r in rows)
    qualities = Counter(str(r["quality"] or "غير محددة") for r in rows)
    top_web = websites.most_common(3)
    top_media = media.most_common(3)
    top_quality = qualities.most_common(3)
    segment = _segment(int(user["downloads"] or 0), active_days, user["last_seen"])

    location = _safe(user["country"], "غير محدد")
    if user["country_code"]:
        location += f" ({_safe(user['country_code'])})"
    source = "دقيق — مشاركة موقع المستخدم" if user["country_source"] == "telegram_location" else "غير مؤكد"

    lines = [
        "🧠 <b>ذكاء المستخدم</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 {_safe(user['username'], _safe(user['first_name'], str(user_id)))}",
        f"🆔 <code>{user_id}</code>",
        f"🏷️ التصنيف: <b>{segment}</b>",
        f"📥 الطلبات المسجلة: {total}",
        "📊 نجاح الطلبات: غير متاح حاليًا — السجل لا يحتوي حالة نجاح/فشل مستقلة",
        f"📅 أيام النشاط: {active_days}",
        f"🕒 آخر نشاط: {_safe(user['last_seen'])}",
        "",
        f"🌍 البلد: <b>{location}</b>",
        f"📌 مصدر البلد: {source}",
    ]
    if user["location_updated_at"]:
        lines.append(f"📍 آخر تحديث للموقع: {_safe(user['location_updated_at'])}")

    lines += ["", "🌐 <b>أكثر المنصات</b>"]
    lines += [f"• {_safe(name)}: {count}" for name, count in top_web] or ["• لا توجد بيانات"]
    lines += ["", "🎬 <b>أنواع الوسائط</b>"]
    lines += [f"• {_safe(name)}: {count}" for name, count in top_media] or ["• لا توجد بيانات"]
    lines += ["", "🎚 <b>الجودات الأكثر استخدامًا</b>"]
    lines += [f"• {_safe(name)}: {count}" for name, count in top_quality] or ["• لا توجد بيانات"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 طلب تحديد البلد بدقة", callback_data=f"{_PREFIX}request_location_{user_id}")],
        [InlineKeyboardButton("🕘 النشاط", callback_data=f"admin_users_plus_activity_{user_id}"), InlineKeyboardButton("👤 التفاصيل", callback_data=f"admin_user_view_{user_id}")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
    ])
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=keyboard)


async def request_location_callback(update, context, bot_module, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    try:
        user_id = int((query.data or "").rsplit("_", 1)[1])
    except (ValueError, IndexError):
        return
    conn = get_db()
    try:
        row = conn.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        await query.edit_message_text("❌ المستخدم غير موجود.")
        return
    language = str(row["language"] or "ar")
    try:
        await request_location_to_user(context.bot, user_id, language)
    except Exception:
        await query.answer("❌ تعذر إرسال طلب الموقع.", show_alert=True)
        return
    await query.answer("📍 تم إرسال طلب الموقع للمستخدم.", show_alert=True)


def register_admin_user_intelligence(app, get_db, owner_id: int, bot_module: Any) -> None:
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: intelligence_callback(u, c, get_db, owner_id),
            pattern=rf"^{_PREFIX}view_\d+$",
        ),
        group=-2,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: request_location_callback(u, c, bot_module, get_db, owner_id),
            pattern=rf"^{_PREFIX}request_location_\d+$",
        ),
        group=-2,
    )
