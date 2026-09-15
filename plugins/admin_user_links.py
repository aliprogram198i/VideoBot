"""Admin user archive and download-link history owner."""

from __future__ import annotations

import html
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes

_LINK_PAGE_RE = re.compile(r"^admin_user_links_(\d+)_([0-9]+)$")
_ARCHIVE_PAGE_RE = re.compile(r"^admin_user_archive_(\d+)_([0-9]+)$")
_PAGE_SIZE = 5
_MAX_TEXT = 3900


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _name(row: Any) -> str:
    if row["username"]:
        return f"@{html.escape(str(row['username']))}"
    value = " ".join(str(p).strip() for p in (row["first_name"], row["last_name"]) if p)
    return html.escape(value[:80] or f"ID {row['user_id']}")


def _keyboard(user_id: int, offset: int, has_next: bool, archive: bool) -> InlineKeyboardMarkup:
    prefix = "admin_user_archive" if archive else "admin_user_links"
    rows = []
    if offset > 0:
        rows.append([InlineKeyboardButton("⬅️ السابق", callback_data=f"{prefix}_{user_id}_{max(0, offset - _PAGE_SIZE)}")])
    if has_next:
        rows.append([InlineKeyboardButton("➡️ التالي", callback_data=f"{prefix}_{user_id}_{offset + _PAGE_SIZE}")])
    rows.append([InlineKeyboardButton("🔗 روابط التحميل", callback_data=f"admin_user_links_{user_id}_0")])
    rows.append([InlineKeyboardButton("🗃️ أرشيف المستخدم", callback_data=f"admin_user_archive_{user_id}_0")])
    rows.append([InlineKeyboardButton("🧠 ذكاء المستخدم", callback_data=f"admin_user_intel_view_{user_id}")])
    rows.append([InlineKeyboardButton("🧹 مسح سجل التحميلات", callback_data=f"admin_user_clear_{user_id}")])
    rows.append([InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")])
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)


async def _render(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int, user_id: int, offset: int = 0, archive: bool = False) -> None:
    query = update.callback_query
    if not _authorized(update, owner_id):
        await query.answer()
        return
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT user_id, username, first_name, last_name, downloads, language, country, country_code, country_source, last_seen, is_banned FROM users WHERE user_id = ?", (user_id,),
        ).fetchone()
        if not user:
            await query.edit_message_text("❌ المستخدم غير موجود.")
            return
        total = int(conn.execute("SELECT COUNT(*) FROM downloads WHERE user_id = ?", (user_id,)).fetchone()[0])
        rows = conn.execute(
            "SELECT id, url, website, media_type, quality, title, created_at FROM downloads "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, _PAGE_SIZE + 1, max(0, offset)),
        ).fetchall()
    finally:
        conn.close()

    has_next = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]
    username = f"@{html.escape(str(user['username']))}" if user["username"] else "غير متوفر"
    name = _name(user)
    country = html.escape(str(user["country"] or "غير محدد"))
    if user["country_code"]:
        country += f" ({html.escape(str(user['country_code']))})"
    source = "دقيق — مشاركة الموقع" if user["country_source"] == "telegram_location" else "غير مؤكد"
    title = "🗃️ أرشيف المستخدم" if archive else "🔗 روابط التحميل"
    lines = [
        f"{title}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🆔 ID: <code>{user_id}</code>",
        f"👤 الاسم: {name}",
        f"🔗 المعرف: {username}",
        f"🌐 اللغة: {html.escape(str(user['language'] or 'غير محددة'))}",
        f"📍 البلد: {country}",
        f"📌 مصدر البلد: {source}",
        f"📥 إجمالي السجل: <b>{total}</b> عملية",
        f"🕒 آخر ظهور: {html.escape(str(user['last_seen'] or 'غير متوفر'))}",
        f"🚦 الحالة: {'🚫 محظور' if user['is_banned'] else '🟢 نشط'}",
        "",
        "📜 <b>سجل العمليات</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not rows:
        lines.append("لا توجد عمليات تحميل مسجلة لهذا المستخدم.")
    else:
        for index, row in enumerate(rows, offset + 1):
            raw_url = str(row["url"] or "").strip()
            if not raw_url:
                continue
            url = html.escape(raw_url, quote=True)
            website = html.escape(str(row["website"] or "غير معروف"))
            media = html.escape(str(row["media_type"] or "غير محدد"))
            quality = html.escape(str(row["quality"] or "غير محددة"))
            item_title = html.escape(str(row["title"] or "بدون عنوان"))[:100]
            created = html.escape(str(row["created_at"] or "غير متوفر"))
            lines.append(f"<b>{index}. {item_title}</b>\n🔗 <a href=\"{url}\">فتح الرابط</a>\n🌐 {website} • {media} • {quality}\n🕒 {created}")

    await query.edit_message_text(
        "\n".join(lines)[:_MAX_TEXT],
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_keyboard(user_id, offset, has_next, archive),
    )


async def _page(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    if not _authorized(update, owner_id):
        await query.answer()
        return
    data = query.data or ""
    match = _LINK_PAGE_RE.match(data)
    archive = False
    if not match:
        match = _ARCHIVE_PAGE_RE.match(data)
        archive = True
    if not match:
        await query.answer()
        return
    await query.answer()
    await _render(update, context, get_db, owner_id, int(match.group(1)), int(match.group(2)), archive=archive)
    raise ApplicationHandlerStop


def register_admin_user_links(app, get_db, owner_id: int) -> None:
    """Register both the legacy links route and canonical per-user archive route."""
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: _page(u, c, get_db, owner_id),
            pattern=r"^(?:admin_user_links|admin_user_archive)_\d+_[0-9]+$",
        ),
        group=-200,
    )
