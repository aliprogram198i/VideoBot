"""Owner-only global download-link history viewer.

Uses the existing downloads table so no destructive migration is required.
Each entry shows the original link as a clickable title plus the Telegram user
who initiated the download and the recorded download metadata.
"""

from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

_PAGE_SIZE = 8
_MAX_TEXT = 3900


def _authorized(update: Update, owner_id: int) -> bool:
    return bool(update.effective_user and update.effective_user.id == owner_id)


def _user_label(row: Any) -> str:
    username = row["username"]
    if username:
        return f"@{html.escape(str(username))}"
    name = " ".join(str(v).strip() for v in (row["first_name"], row["last_name"]) if v)
    return html.escape(name[:60] or f"ID {row['user_id']}")


def _keyboard(offset: int, has_next: bool) -> InlineKeyboardMarkup:
    nav = []
    if offset:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_download_log_{max(0, offset - _PAGE_SIZE)}"))
    if has_next:
        nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"admin_download_log_{offset + _PAGE_SIZE}"))
    rows = [nav] if nav else []
    rows += [
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_page_0")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
    ]
    return InlineKeyboardMarkup(rows)


async def _render(update: Update, get_db, owner_id: int, offset: int) -> None:
    query = update.callback_query
    if not _authorized(update, owner_id):
        await query.answer()
        return
    conn = get_db()
    try:
        total = int(conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0])
        rows = conn.execute(
            "SELECT d.id, d.user_id, d.username, d.url, d.website, d.media_type, "
            "d.quality, d.created_at, u.first_name, u.last_name "
            "FROM downloads d LEFT JOIN users u ON u.user_id = d.user_id "
            "ORDER BY d.id DESC LIMIT ? OFFSET ?",
            (_PAGE_SIZE + 1, max(0, offset)),
        ).fetchall()
    finally:
        conn.close()

    has_next = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]
    lines = [
        "🔗 <b>سجل الروابط المحمّلة</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🧾 إجمالي العمليات المسجلة: <b>{total}</b>",
        f"📄 عرض: <b>{offset + 1 if rows else 0}–{offset + len(rows)}</b>",
        "",
    ]
    if not rows:
        lines.append("لا توجد عمليات تحميل مسجلة.")
    else:
        for index, row in enumerate(rows, offset + 1):
            raw_url = str(row["url"] or "").strip()
            if not raw_url:
                continue
            safe_url = html.escape(raw_url, quote=True)
            website = html.escape(str(row["website"] or "غير معروف"))
            media = html.escape(str(row["media_type"] or ""))
            quality = html.escape(str(row["quality"] or ""))
            created = html.escape(str(row["created_at"] or ""))
            user = _user_label(row)
            candidate = (
                f"<b>{index}. <a href=\"{safe_url}\">{website} — فتح الرابط</a></b>\n"
                f"   👤 المستخدم: {user} (<code>{int(row['user_id'])}</code>)\n"
                f"   🔗 العنوان: <code>{safe_url[:220]}</code>\n"
                f"   ⚙️ {media} • {quality} • 🕒 {created}\n\n"
            )
            if len("\n".join(lines)) + len(candidate) > _MAX_TEXT:
                break
            lines.append(candidate)

    await query.edit_message_text(
        "\n".join(lines)[:_MAX_TEXT],
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_keyboard(offset, has_next),
    )


async def callback(update: Update, context, get_db, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    try:
        offset = int((query.data or "").rsplit("_", 1)[1])
    except (ValueError, IndexError):
        return
    await _render(update, get_db, owner_id, max(0, offset))
    raise ApplicationHandlerStop


def register_admin_download_log(app: Any, get_db, owner_id: int) -> None:
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: callback(u, c, get_db, owner_id),
            pattern=r"^admin_download_log_[0-9]+$",
        ),
        group=-200,
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: _render(u, get_db, owner_id, 0),
            pattern=r"^admin_records$",
        ),
        group=-200,
    )
