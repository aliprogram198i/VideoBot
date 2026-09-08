"""Admin user-detail download links owner.

The user-detail route is shared with the legacy history layer. This module
makes the links view the single owner of both legacy and current user-detail
callbacks so another handler cannot overwrite the rendered message.
"""

from __future__ import annotations

import html
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes

_USER_RE = re.compile(r"^(?:admin_user_view|user)_(\d+)$")
_LINK_PAGE_RE = re.compile(r"^admin_user_links_(\d+)_([0-9]+)$")
_PAGE_SIZE = 5
_MAX_TEXT = 3900
_DETAIL_PATTERNS = (
    r"^(?:admin_user_view|user)_\d+$",
)


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _name(row: Any) -> str:
    if row["username"]:
        return f"@{html.escape(str(row['username']))}"
    value = " ".join(str(p).strip() for p in (row["first_name"], row["last_name"]) if p)
    return html.escape(value[:80] or f"ID {row['user_id']}")


def _keyboard(user_id: int, offset: int, has_next: bool) -> InlineKeyboardMarkup:
    rows = []
    if offset > 0:
        rows.append([InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_user_links_{user_id}_{max(0, offset - _PAGE_SIZE)}")])
    if has_next:
        rows.append([InlineKeyboardButton("🔗 المزيد من الروابط", callback_data=f"admin_user_links_{user_id}_{offset + _PAGE_SIZE}")])
    rows.append([InlineKeyboardButton("🧹 مسح سجل التحميلات", callback_data=f"admin_user_clear_{user_id}")])
    rows.append([InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")])
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)


def _remove_competing_detail_handlers(app: Any) -> int:
    """Remove every pre-existing user-detail callback before taking ownership."""
    removed = 0
    handlers_by_group = getattr(app, "handlers", {})
    for group, handlers in list(handlers_by_group.items()):
        kept = []
        for handler in handlers:
            if not isinstance(handler, CallbackQueryHandler):
                kept.append(handler)
                continue
            pattern = getattr(handler, "pattern", None)
            pattern_text = getattr(pattern, "pattern", None) or (pattern if isinstance(pattern, str) else "")
            if pattern_text in _DETAIL_PATTERNS:
                removed += 1
                continue
            kept.append(handler)
        handlers_by_group[group] = kept
    return removed


async def _render(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int, user_id: int, offset: int = 0) -> None:
    query = update.callback_query
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT user_id, username, first_name, last_name, downloads, language, country, last_seen, is_banned "
            "FROM users WHERE user_id = ?", (user_id,),
        ).fetchone()
        if not user:
            await query.edit_message_text("❌ المستخدم غير موجود.")
            return
        rows = conn.execute(
            "SELECT id, url, website, media_type, quality, created_at FROM downloads "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, _PAGE_SIZE + 1, max(0, offset)),
        ).fetchall()
    finally:
        conn.close()

    has_next = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]
    username = f"@{html.escape(str(user['username']))}" if user["username"] else "غير متوفر"
    name = _name(user)
    lines = [
        "👤 <b>تفاصيل المستخدم</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🆔 ID: <code>{user_id}</code>",
        f"👤 الاسم: {name}",
        f"🔗 المعرف: {username}",
        f"🌐 اللغة: {html.escape(str(user['language'] or 'غير محددة'))}",
        f"📍 البلد: {html.escape(str(user['country'] or 'غير محدد'))}",
        f"📥 العداد: {int(user['downloads'] or 0)}",
        f"🧾 السجل الفعلي: {int(user['downloads'] or 0)} عملية" if not rows and int(user['downloads'] or 0) else f"🧾 السجل الفعلي: {len(rows)} عملية" + (" + المزيد" if has_next else ""),
        f"🕒 آخر ظهور: {html.escape(str(user['last_seen'] or 'غير متوفر'))}",
        f"🚦 الحالة: {'🚫 محظور' if user['is_banned'] else '🟢 نشط'}",
        "",
        "🔗 <b>روابط التحميل</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not rows:
        lines.append("لا توجد روابط تحميل مسجلة لهذا المستخدم.")
    else:
        for index, row in enumerate(rows, offset + 1):
            raw_url = str(row["url"] or "").strip()
            if not raw_url:
                continue
            url = html.escape(raw_url, quote=True)
            website = html.escape(str(row["website"] or "غير معروف"))
            media = html.escape(str(row["media_type"] or ""))
            quality = html.escape(str(row["quality"] or ""))
            created = html.escape(str(row["created_at"] or ""))
            meta = " • ".join(x for x in (website, media, quality, created) if x)
            candidate = f"{index}. <a href=\"{url}\">{website}</a>\n   <code>{url}</code>\n   {meta}"
            if sum(len(x) + 1 for x in lines) + len(candidate) > _MAX_TEXT:
                break
            lines.append(candidate)

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_keyboard(user_id, offset, has_next),
    )


async def _view(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    if not _authorized(update, owner_id):
        await query.answer()
        return
    match = _USER_RE.match(query.data or "")
    if not match:
        await query.answer()
        return
    await query.answer()
    await _render(update, context, get_db, owner_id, int(match.group(1)), 0)
    raise ApplicationHandlerStop


async def _page(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db, owner_id: int) -> None:
    query = update.callback_query
    if not _authorized(update, owner_id):
        await query.answer()
        return
    match = _LINK_PAGE_RE.match(query.data or "")
    if not match:
        await query.answer()
        return
    await query.answer()
    await _render(update, context, get_db, owner_id, int(match.group(1)), int(match.group(2)))
    raise ApplicationHandlerStop


def register_admin_user_links(app, get_db, owner_id: int) -> None:
    """Own user-detail callbacks exclusively and prevent message overwrites."""
    removed = _remove_competing_detail_handlers(app)
    if removed:
        print(f"🧩 Admin user-links: removed {removed} competing user-detail handler(s)", flush=True)

    app.add_handler(
        CallbackQueryHandler(lambda u, c: _view(u, c, get_db, owner_id), pattern=r"^(?:admin_user_view|user)_\d+$"),
        group=-200,
    )
    app.add_handler(
        CallbackQueryHandler(lambda u, c: _page(u, c, get_db, owner_id), pattern=r"^admin_user_links_\d+_[0-9]+$"),
        group=-200,
    )
