"""Isolated AI operations for the administrative control center.

This module intentionally owns the ``admin_ai`` callback family so the new
admin layer does not depend on the legacy AI handlers embedded in ``bot.py``.
It receives the database accessor and owner id from the control center and
keeps all Gemini access optional and fail-closed.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from .admin_common import authorize

logger = logging.getLogger(__name__)

try:
    from google import genai
except ImportError:  # pragma: no cover - optional dependency
    genai = None


_MODEL = "gemini-3.6-flash"


def _client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or genai is None:
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception as exc:  # pragma: no cover - provider initialization
        logger.warning("Gemini admin client initialization failed: %s", type(exc).__name__)
        return None


async def _generate(prompt: str) -> str:
    client = _client()
    if client is None:
        raise RuntimeError("Gemini AI is not configured")

    def generate() -> str:
        response = client.models.generate_content(model=_MODEL, contents=prompt)
        return response.text or ""

    return (await asyncio.to_thread(generate)).strip()


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 تحليل الإحصائيات", callback_data="ai_stats")],
        [InlineKeyboardButton("🧪 اختبار Gemini", callback_data="ai_test")],
        [InlineKeyboardButton("📈 تقرير الأداء", callback_data="ai_report")],
        [InlineKeyboardButton("👥 تحليل المستخدمين", callback_data="ai_users")],
        [InlineKeyboardButton("🌐 تحليل المنصات", callback_data="ai_websites")],
        [InlineKeyboardButton("🐞 تقرير الأخطاء", callback_data="ai_errors")],
        [InlineKeyboardButton("🔙 مركز التحكم", callback_data="admin_control_center")],
    ])


def _back_keyboard(retry: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if retry:
        rows.append([InlineKeyboardButton("🔄 إعادة المحاولة", callback_data=retry)])
    rows.extend([
        [InlineKeyboardButton("🔙 الذكاء الاصطناعي", callback_data="admin_ai")],
        [InlineKeyboardButton("🎛️ مركز التحكم", callback_data="admin_control_center")],
    ])
    return InlineKeyboardMarkup(rows)


def _stats(get_db: Any) -> dict[str, Any]:
    conn = get_db()
    try:
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        banned = conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_banned = 1").fetchone()["n"]
        downloads = conn.execute("SELECT COUNT(*) AS n FROM downloads").fetchone()["n"]
        videos = conn.execute("SELECT COUNT(*) AS n FROM downloads WHERE media_type = 'video'").fetchone()["n"]
        audio = conn.execute("SELECT COUNT(*) AS n FROM downloads WHERE media_type = 'audio'").fetchone()["n"]
        websites = [dict(row) for row in conn.execute(
            "SELECT website, COUNT(*) AS count FROM downloads GROUP BY website ORDER BY count DESC LIMIT 10"
        ).fetchall()]
        languages = [dict(row) for row in conn.execute(
            "SELECT language, COUNT(*) AS count FROM users GROUP BY language ORDER BY count DESC"
        ).fetchall()]
        return {
            "users": users or 0,
            "banned": banned or 0,
            "downloads": downloads or 0,
            "videos": videos or 0,
            "audio": audio or 0,
            "websites": websites,
            "languages": languages,
        }
    finally:
        conn.close()


def _users(get_db: Any) -> dict[str, Any]:
    conn = get_db()
    try:
        summary = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN is_banned = 1 THEN 1 ELSE 0 END) AS banned,
                SUM(CASE WHEN downloads > 0 THEN 1 ELSE 0 END) AS active,
                AVG(downloads) AS average_downloads,
                MAX(downloads) AS max_downloads
            FROM users
        """).fetchone()
        languages = [dict(row) for row in conn.execute(
            "SELECT language, COUNT(*) AS count FROM users GROUP BY language ORDER BY count DESC"
        ).fetchall()]
        top_users = [dict(row) for row in conn.execute("""
            SELECT user_id, username, first_name, downloads
            FROM users
            ORDER BY downloads DESC, last_seen DESC
            LIMIT 10
        """).fetchall()]
        return {
            "total": summary["total"] or 0,
            "banned": summary["banned"] or 0,
            "active": summary["active"] or 0,
            "average_downloads": round(summary["average_downloads"] or 0, 2),
            "max_downloads": summary["max_downloads"] or 0,
            "languages": languages,
            "top_users": top_users,
        }
    finally:
        conn.close()


def _websites(get_db: Any) -> dict[str, Any]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT website, COUNT(*) AS count FROM downloads GROUP BY website ORDER BY count DESC"
        ).fetchall()
        total = sum(row["count"] or 0 for row in rows)
        return {
            "total_downloads": total,
            "websites": [
                {
                    "website": row["website"] or "unknown",
                    "downloads": row["count"] or 0,
                    "percentage": round(((row["count"] or 0) / total * 100) if total else 0, 2),
                }
                for row in rows
            ],
        }
    finally:
        conn.close()


def _report(get_db: Any) -> dict[str, Any]:
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    conn = get_db()
    try:
        total_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] or 0
        banned_users = conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_banned = 1").fetchone()["n"] or 0
        active_users = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE last_seen IS NOT NULL AND last_seen >= ?",
            (cutoff,),
        ).fetchone()["n"] or 0
        period_downloads = conn.execute(
            "SELECT COUNT(*) AS n FROM downloads WHERE created_at >= ?", (cutoff,)
        ).fetchone()["n"] or 0
        period_videos = conn.execute(
            "SELECT COUNT(*) AS n FROM downloads WHERE media_type = 'video' AND created_at >= ?",
            (cutoff,),
        ).fetchone()["n"] or 0
        period_audio = conn.execute(
            "SELECT COUNT(*) AS n FROM downloads WHERE media_type = 'audio' AND created_at >= ?",
            (cutoff,),
        ).fetchone()["n"] or 0
        websites = [dict(row) for row in conn.execute(
            "SELECT website, COUNT(*) AS count FROM downloads WHERE created_at >= ? GROUP BY website ORDER BY count DESC LIMIT 10",
            (cutoff,),
        ).fetchall()]
        return {
            "period_days": 30,
            "total_users": total_users,
            "banned_users": banned_users,
            "active_users": active_users,
            "period_downloads": period_downloads,
            "period_videos": period_videos,
            "period_audio": period_audio,
            "websites": websites,
        }
    finally:
        conn.close()


def _errors(get_db: Any, days: int = 30) -> dict[str, Any]:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM error_logs WHERE created_at >= ?", (cutoff,)
        ).fetchone()["n"] or 0
        websites = [dict(row) for row in conn.execute(
            "SELECT website, COUNT(*) AS count FROM error_logs WHERE created_at >= ? GROUP BY website ORDER BY count DESC LIMIT 10",
            (cutoff,),
        ).fetchall()]
        stages = [dict(row) for row in conn.execute(
            "SELECT stage, COUNT(*) AS count FROM error_logs WHERE created_at >= ? GROUP BY stage ORDER BY count DESC LIMIT 10",
            (cutoff,),
        ).fetchall()]
        types = [dict(row) for row in conn.execute(
            "SELECT error_type, COUNT(*) AS count FROM error_logs WHERE created_at >= ? GROUP BY error_type ORDER BY count DESC LIMIT 10",
            (cutoff,),
        ).fetchall()]
        recent = [dict(row) for row in conn.execute("""
            SELECT website, media_type, stage, error_type, error_message, created_at
            FROM error_logs
            WHERE created_at >= ?
            ORDER BY id DESC
            LIMIT 20
        """, (cutoff,)).fetchall()]
        for row in recent:
            row["error_message"] = str(row.get("error_message") or "")[:800]
        return {
            "period_days": days,
            "total_error_records": total,
            "websites": websites,
            "stages": stages,
            "error_types": types,
            "recent_errors": recent,
        }
    finally:
        conn.close()


async def _show_analysis(query: Any, title: str, prompt: str, retry: str) -> None:
    await query.edit_message_text("🤖 Gemini AI\n━━━━━━━━━━━━━━━━━━\n\n⏳ جاري تحليل البيانات...")
    try:
        result = await _generate(prompt)
        if not result:
            result = "لم يُرجع Gemini نتيجة."
        if len(result) > 3900:
            result = result[:3900] + "\n\n… تم اختصار التقرير."
        await query.edit_message_text(
            f"{title}\n━━━━━━━━━━━━━━━━━━\n\n{result}",
            reply_markup=_back_keyboard(),
        )
    except Exception as exc:
        logger.exception("Gemini admin analysis failed: %s", type(exc).__name__)
        await query.edit_message_text(
            "🤖 Gemini AI\n━━━━━━━━━━━━━━━━━━\n\n"
            "❌ تعذر تنفيذ التحليل.\n\n"
            f"نوع الخطأ: {type(exc).__name__}",
            reply_markup=_back_keyboard(retry),
        )


def _authorized(update: Update, get_db: Any, owner_id: int) -> bool:
    return authorize(update, get_db, owner_id, "center.view")


async def ai_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    await query.edit_message_text(
        "🤖 <b>الذكاء الاصطناعي</b>\n━━━━━━━━━━━━━━━━━━\n\nاختر خدمة التحليل المطلوبة:",
        parse_mode="HTML",
        reply_markup=_keyboard(),
    )


async def ai_test_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    await query.edit_message_text("🤖 جاري اختبار اتصال Gemini...")
    try:
        result = await _generate("أجب بكلمة واحدة فقط: متصل")
        result = result or ""
        if len(result) > 500:
            result = result[:500]
        await query.edit_message_text(
            "🤖 <b>اختبار Gemini</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "✅ الاتصال ناجح\n\n"
            f"الرد: {html.escape(result)}",
            parse_mode="HTML",
            reply_markup=_back_keyboard("ai_test"),
        )
    except Exception as exc:
        await query.edit_message_text(
            "🤖 <b>اختبار Gemini</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "❌ الاتصال غير متاح حالياً.\n\n"
            f"نوع الخطأ: {type(exc).__name__}",
            parse_mode="HTML",
            reply_markup=_back_keyboard("ai_test"),
        )


async def ai_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    data = _stats(get_db)
    prompt = f"""أنت محلل بيانات ومدير تقني لبوت Telegram لتحميل الوسائط.
حلل البيانات التالية فقط:
{data}

اكتب تقريراً عربياً مختصراً يتضمن الحالة، أهم الأرقام، مستوى النشاط، أنواع الاستخدام، الملاحظات، و3 توصيات عملية.
لا تخترع أرقاماً ولا تكشف معلومات سرية أو تحدد هوية الأشخاص."""
    await _show_analysis(query, "📊 تحليل الإحصائيات", prompt, "ai_stats")


async def ai_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    data = _report(get_db)
    prompt = f"""أنت مستشار تقني متخصص في تحليل أداء بوتات Telegram.
هذه بيانات آخر 30 يوماً فقط:
{data}

أنشئ تقريراً عربياً احترافياً عن الأداء، نشاط المستخدمين، التحميلات، الفيديو والصوت، المنصات، نقاط القوة والضعف، و5 توصيات.
اعتمد على البيانات فقط ولا تخترع أرقاماً."""
    await _show_analysis(query, "📈 تقرير الأداء", prompt, "ai_report")


async def ai_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    data = _users(get_db)
    prompt = f"""أنت محلل بيانات لبوت Telegram.
حلل بيانات المستخدمين التالية فقط:
{data}

اذكر حجم القاعدة، النشاط، المحظورين، متوسط التحميلات، توزيع اللغات، والملاحظات الإدارية و3 اقتراحات.
لا تحاول تحديد هوية الأشخاص ولا تخترع أرقاماً."""
    await _show_analysis(query, "👥 تحليل المستخدمين", prompt, "ai_users")


async def ai_websites_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    data = _websites(get_db)
    prompt = f"""أنت محلل استخدام لمنصة تحميل Telegram.
حلل بيانات المنصات التالية فقط:
{data}

رتب المنصات حسب الاستخدام، اذكر النسب، التنوع، ومقترحات تحسين دعم المنصات الأكثر استخداماً.
لا تخترع أي بيانات."""
    await _show_analysis(query, "🌐 تحليل المنصات", prompt, "ai_websites")


async def ai_errors_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    try:
        data = _errors(get_db)
        if data["total_error_records"] == 0:
            await query.edit_message_text(
                "🐞 <b>تقرير الأخطاء</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                "✅ لا توجد أخطاء مسجلة خلال آخر 30 يوماً.",
                parse_mode="HTML",
                reply_markup=_back_keyboard("ai_errors"),
            )
            return
        prompt = f"""أنت مهندس Reliability وBackend متخصص في Telegram bots وyt-dlp وFFmpeg.
هذه بيانات أخطاء حقيقية من آخر 30 يوماً:
{data}

أنشئ تقريراً تقنياً عربياً يحدد الحالة، المنصات، المراحل، أنواع الأخطاء، الأسباب المحتملة، الحلول والأولويات.
استخدم البيانات فقط، لا تخترع أرقاماً أو أخطاء، ولا تكشف أسراراً أو هوية المستخدمين."""
        await _show_analysis(query, "🐞 تقرير الأخطاء", prompt, "ai_errors")
    except Exception as exc:
        logger.exception("AI error report failed: %s", type(exc).__name__)
        await query.edit_message_text(
            "🐞 <b>تقرير الأخطاء</b>\n━━━━━━━━━━━━━━━━━━\n\n❌ تعذر تحميل التقرير.",
            parse_mode="HTML",
            reply_markup=_back_keyboard("ai_errors"),
        )


def register_admin_ai(app: Any, get_db: Any, owner_id: int) -> None:
    """Register the complete admin AI callback family exactly once."""
    routes = (
        (ai_home_callback, r"^admin_ai$"),
        (ai_test_callback, r"^ai_test$"),
        (ai_stats_callback, r"^ai_stats$"),
        (ai_report_callback, r"^ai_report$"),
        (ai_users_callback, r"^ai_users$"),
        (ai_websites_callback, r"^ai_websites$"),
        (ai_errors_callback, r"^ai_errors$"),
    )
    existing = getattr(app, "handlers", {})
    existing_patterns = set()
    for handlers in existing.values():
        for handler in handlers:
            if isinstance(handler, CallbackQueryHandler):
                pattern = getattr(handler, "pattern", None)
                pattern_text = getattr(pattern, "pattern", None) or (pattern if isinstance(pattern, str) else "")
                existing_patterns.add(pattern_text)

    for callback, pattern in routes:
        if pattern in existing_patterns:
            continue
        app.add_handler(
            CallbackQueryHandler(
                lambda u, c, callback=callback: callback(u, c, get_db, owner_id),
                pattern=pattern,
            ),
            group=-2,
        )
        existing_patterns.add(pattern)
