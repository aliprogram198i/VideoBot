"""Consent-based user location collection for accurate country data.

Telegram does not expose a user's country in the normal User object. This module
therefore only records a country after the user explicitly shares their location.
No location is requested automatically.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)
_GEOCODE_LOCK = asyncio.Lock()


def ensure_location_schema(get_db) -> None:
    """Add only the location fields; existing user/download data is untouched."""
    conn = get_db()
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        additions = {
            "country_code": "TEXT",
            "location_latitude": "REAL",
            "location_longitude": "REAL",
            "location_accuracy_m": "REAL",
            "location_updated_at": "TEXT",
            "country_source": "TEXT",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")
        conn.commit()
    finally:
        conn.close()


def _messages(language: str) -> dict[str, str]:
    language = language if language in {"ar", "en", "tr", "de"} else "ar"
    return {
        "button": {"ar": "📍 تحديد البلد بدقة", "en": "📍 Set country accurately", "tr": "📍 Ülkeyi doğru belirle", "de": "📍 Land genau bestimmen"}[language],
        "prompt": {
            "ar": "📍 <b>تحديد البلد بدقة</b>\n\nلمعرفة بلدك بدقة، شارك موقعك الحالي مع AliBot.\n\nسيتم استخدام الموقع لتحديد <b>الدولة فقط</b> ولن يتم عرض الإحداثيات في لوحة الإدارة.",
            "en": "📍 <b>Accurate country</b>\n\nShare your current location with AliBot so we can determine your country accurately.\n\nOnly the <b>country</b> is used in the admin panel; raw coordinates are not displayed there.",
            "tr": "📍 <b>Doğru ülke</b>\n\nÜlkenizi doğru belirlemek için mevcut konumunuzu AliBot ile paylaşın.\n\nYönetim panelinde yalnızca <b>ülke</b> kullanılır; ham koordinatlar gösterilmez.",
            "de": "📍 <b>Genaues Land</b>\n\nTeilen Sie Ihren aktuellen Standort, damit AliBot Ihr Land genau bestimmen kann.\n\nIm Adminbereich wird nur das <b>Land</b> verwendet; Rohkoordinaten werden dort nicht angezeigt.",
        }[language],
        "success": {
            "ar": "✅ تم تحديد بلدك بدقة: <b>{country}</b>.",
            "en": "✅ Your country was identified accurately: <b>{country}</b>.",
            "tr": "✅ Ülkeniz doğru belirlendi: <b>{country}</b>.",
            "de": "✅ Ihr Land wurde genau bestimmt: <b>{country}</b>.",
        }[language],
        "failed": {
            "ar": "⚠️ تعذر تحديد البلد من الموقع حاليًا. حاول مرة أخرى لاحقًا.",
            "en": "⚠️ The country could not be determined from the location right now. Please try again later.",
            "tr": "⚠️ Ülke konumdan şu anda belirlenemedi. Lütfen daha sonra tekrar deneyin.",
            "de": "⚠️ Das Land konnte anhand des Standorts derzeit nicht bestimmt werden. Bitte später erneut versuchen.",
        }[language],
    }


def _request_keyboard(language: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(_messages(language)["button"], request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
    )


async def request_location_to_user(bot, user_id: int, language: str) -> None:
    await bot.send_message(
        chat_id=user_id,
        text=_messages(language)["prompt"],
        parse_mode="HTML",
        reply_markup=_request_keyboard(language),
    )


async def _reverse_geocode(latitude: float, longitude: float) -> tuple[str, str] | None:
    params = urllib.parse.urlencode({
        "lat": f"{latitude:.7f}",
        "lon": f"{longitude:.7f}",
        "format": "jsonv2",
        "zoom": "3",
        "addressdetails": "1",
    })
    request = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/reverse?{params}",
        headers={"User-Agent": "AliBot/1.0 country-resolution"},
    )
    async with _GEOCODE_LOCK:
        def fetch() -> tuple[str, str] | None:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read(512 * 1024).decode("utf-8"))
            address = payload.get("address") or {}
            country = str(address.get("country") or "").strip()
            code = str(address.get("country_code") or "").strip().upper()
            if not country or len(code) != 2:
                return None
            return country, code
        return await asyncio.to_thread(fetch)


async def _location_message(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    user = update.effective_user
    message = update.effective_message
    location = message.location if message else None
    if not user or not message or not location or user.is_bot:
        return
    language = bot_module.get_language(user.id) or "ar"
    try:
        result = await _reverse_geocode(float(location.latitude), float(location.longitude))
    except Exception as exc:
        logger.warning("Country reverse geocoding failed: %s", type(exc).__name__)
        result = None
    if not result:
        await message.reply_text(_messages(language)["failed"], parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return
    country, country_code = result
    ensure_location_schema(bot_module.get_db)
    now = datetime.now(timezone.utc).isoformat()
    conn = bot_module.get_db()
    try:
        conn.execute(
            "UPDATE users SET country = ?, country_code = ?, location_latitude = ?, location_longitude = ?, location_accuracy_m = ?, location_updated_at = ?, country_source = ? WHERE user_id = ?",
            (country, country_code, float(location.latitude), float(location.longitude), float(location.horizontal_accuracy) if location.horizontal_accuracy is not None else None, now, "telegram_location", user.id),
        )
        conn.commit()
    finally:
        conn.close()
    await message.reply_text(_messages(language)["success"].format(country=html.escape(country)), parse_mode="HTML", reply_markup=ReplyKeyboardRemove())


async def _location_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    user = update.effective_user
    if not user or user.is_bot or bot_module.is_banned(user.id):
        return
    language = bot_module.get_language(user.id) or "ar"
    await update.effective_message.reply_text(_messages(language)["prompt"], parse_mode="HTML", reply_markup=_request_keyboard(language))


def register_user_location(app, bot_module: Any) -> None:
    ensure_location_schema(bot_module.get_db)
    app.add_handler(CommandHandler("country", lambda u, c: _location_command(u, c, bot_module), filters=filters.ChatType.PRIVATE), group=-20)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.LOCATION, lambda u, c: _location_message(u, c, bot_module)), group=-20)
