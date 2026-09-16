"""User-facing feature layer for AliBot.

This layer is intentionally isolated from the legacy downloader. It adds:
- a user download-history command and re-download buttons;
- bounded multi-URL batch downloads that reuse the existing download pipeline;
- a safe link-information command that previews public media metadata;
- a small user hub shown from the existing start button.

It never changes the downloader, splitting implementation, database schema,
or existing admin ownership.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from types import SimpleNamespace
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

MAX_BATCH_URLS = 5
MAX_URL_LENGTH = 2048
INFO_TIMEOUT_SECONDS = 25
INFO_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
BATCH_QUALITY_RE = re.compile(r"^(?:video_(?:best|1080|720|480|360)|audio_(?:best|320|256|192|128))$")
BATCH_TYPE_RE = re.compile(r"^(?:video_menu|audio_menu)$")
HISTORY_RE = re.compile(r"^user_history_pick_(\d+)$")

_BATCH_LOCKS: dict[int, asyncio.Lock] = {}


def _lock_for(user_id: int) -> asyncio.Lock:
    lock = _BATCH_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _BATCH_LOCKS[user_id] = lock
    return lock


def _language(bot_module: Any, user_id: int) -> str:
    return bot_module.get_language(user_id) or "ar"


def _messages(language: str) -> dict[str, str]:
    return {
        "hub": {
            "ar": "🎬 <b>AliBot</b>\n━━━━━━━━━━━━━━━━━━\n\nاختر ما تريد القيام به:",
            "en": "🎬 <b>AliBot</b>\n━━━━━━━━━━━━━━━━━━\n\nChoose an action:",
            "tr": "🎬 <b>AliBot</b>\n━━━━━━━━━━━━━━━━━━\n\nBir işlem seçin:",
            "de": "🎬 <b>AliBot</b>\n━━━━━━━━━━━━━━━━━━\n\nWählen Sie eine Aktion:",
        }[language],
        "single": {
            "ar": "🎬 أرسل رابط الفيديو أو الصوت الآن.",
            "en": "🎬 Send your video or audio link now.",
            "tr": "🎬 Video veya ses bağlantınızı gönderin.",
            "de": "🎬 Senden Sie jetzt Ihren Video- oder Audiolink.",
        }[language],
        "batch_prompt": {
            "ar": f"📦 <b>التحميل المتعدد</b>\n\nأرسل من 2 إلى {MAX_BATCH_URLS} روابط HTTP(S) في رسالة واحدة، وسيتم تحميلها بالتتابع عبر نفس مسار AliBot الحالي.",
            "en": f"📦 <b>Batch download</b>\n\nSend 2 to {MAX_BATCH_URLS} HTTP(S) links in one message. They will be processed sequentially through AliBot's existing pipeline.",
            "tr": f"📦 <b>Toplu indirme</b>\n\nTek mesajda 2-{MAX_BATCH_URLS} HTTP(S) bağlantısı gönderin. Bağlantılar mevcut AliBot indirme hattından sırayla işlenir.",
            "de": f"📦 <b>Stapel-Download</b>\n\nSenden Sie 2 bis {MAX_BATCH_URLS} HTTP(S)-Links in einer Nachricht. Sie werden nacheinander über die bestehende AliBot-Pipeline verarbeitet.",
        }[language],
        "history_empty": {
            "ar": "📚 <b>سجل التنزيلات</b>\n\nلا توجد تنزيلات محفوظة حتى الآن.",
            "en": "📚 <b>Download history</b>\n\nNo saved downloads yet.",
            "tr": "📚 <b>İndirme geçmişi</b>\n\nHenüz kayıtlı indirme yok.",
            "de": "📚 <b>Download-Verlauf</b>\n\nNoch keine gespeicherten Downloads.",
        }[language],
        "history_title": {
            "ar": "📚 <b>آخر التنزيلات</b>\n━━━━━━━━━━━━━━━━━━\n\n",
            "en": "📚 <b>Recent downloads</b>\n━━━━━━━━━━━━━━━━━━\n\n",
            "tr": "📚 <b>Son indirmeler</b>\n━━━━━━━━━━━━━━━━━━\n\n",
            "de": "📚 <b>Letzte Downloads</b>\n━━━━━━━━━━━━━━━━━━\n\n",
        }[language],
        "batch_busy": {
            "ar": "⏳ لديك عملية تحميل متعددة قيد التنفيذ بالفعل.",
            "en": "⏳ You already have a batch download in progress.",
            "tr": "⏳ Zaten devam eden bir toplu indirme işleminiz var.",
            "de": "⏳ Sie haben bereits einen laufenden Stapel-Download.",
        }[language],
        "batch_ready": {
            "ar": "📦 تم استلام {count} روابط.\n\n👇 اختر نوع التحميل:",
            "en": "📦 Received {count} links.\n\n👇 Choose the download type:",
            "tr": "📦 {count} bağlantı alındı.\n\n👇 İndirme türünü seçin:",
            "de": "📦 {count} Links erhalten.\n\n👇 Wählen Sie den Download-Typ:",
        }[language],
        "batch_done": {
            "ar": "📦 <b>اكتمل التحميل المتعدد</b>\n━━━━━━━━━━━━━━━━━━\n\nتمت معالجة {count} روابط عبر مسار التنزيل الحالي.",
            "en": "📦 <b>Batch download complete</b>\n━━━━━━━━━━━━━━━━━━\n\nProcessed {count} links through the existing download pipeline.",
            "tr": "📦 <b>Toplu indirme tamamlandı</b>\n━━━━━━━━━━━━━━━━━━\n\n{count} bağlantı mevcut indirme hattından işlendi.",
            "de": "📦 <b>Stapel-Download abgeschlossen</b>\n━━━━━━━━━━━━━━━━━━\n\n{count} Links wurden über die bestehende Download-Pipeline verarbeitet.",
        }[language],
        "invalid_batch": {
            "ar": f"❌ يجب إرسال من 2 إلى {MAX_BATCH_URLS} روابط HTTP(S) صحيحة في رسالة واحدة.",
            "en": f"❌ Send 2 to {MAX_BATCH_URLS} valid HTTP(S) links in one message.",
            "tr": f"❌ Tek mesajda 2-{MAX_BATCH_URLS} geçerli HTTP(S) bağlantısı gönderin.",
            "de": f"❌ Senden Sie 2 bis {MAX_BATCH_URLS} gültige HTTP(S)-Links in einer Nachricht.",
        }[language],
        "info_prompt": {
            "ar": "ℹ️ <b>معلومات الرابط</b>\n\nأرسل الأمر بهذا الشكل:\n<code>/info https://example.com/video</code>\n\nسيعرض AliBot المعلومات المتاحة دون تنزيل الملف.",
            "en": "ℹ️ <b>Link information</b>\n\nUse the command like this:\n<code>/info https://example.com/video</code>\n\nAliBot will show available metadata without downloading the file.",
            "tr": "ℹ️ <b>Bağlantı bilgisi</b>\n\nKomutu şu şekilde kullanın:\n<code>/info https://example.com/video</code>\n\nAliBot dosyayı indirmeden mevcut bilgileri gösterir.",
            "de": "ℹ️ <b>Linkinformationen</b>\n\nVerwenden Sie den Befehl so:\n<code>/info https://example.com/video</code>\n\nAliBot zeigt verfügbare Metadaten, ohne die Datei herunterzuladen.",
        }[language],
        "info_usage": {
            "ar": "ℹ️ أرسل رابطًا بعد الأمر:\n<code>/info https://example.com/video</code>",
            "en": "ℹ️ Add a URL after the command:\n<code>/info https://example.com/video</code>",
            "tr": "ℹ️ Komuttan sonra bir URL ekleyin:\n<code>/info https://example.com/video</code>",
            "de": "ℹ️ Fügen Sie eine URL nach dem Befehl ein:\n<code>/info https://example.com/video</code>",
        }[language],
        "info_processing": {
            "ar": "🔎 جاري تحليل الرابط دون تنزيل الملف...",
            "en": "🔎 Analyzing the link without downloading the file...",
            "tr": "🔎 Dosya indirilmeden bağlantı analiz ediliyor...",
            "de": "🔎 Der Link wird analysiert, ohne die Datei herunterzuladen...",
        }[language],
        "info_failed": {
            "ar": "❌ تعذر قراءة معلومات هذا الرابط الآن. تأكد من الرابط وحاول مرة أخرى.",
            "en": "❌ Could not read this link's information right now. Check the URL and try again.",
            "tr": "❌ Bu bağlantının bilgileri şu anda okunamadı. URL'yi kontrol edip tekrar deneyin.",
            "de": "❌ Die Informationen dieses Links konnten gerade nicht gelesen werden. Prüfen Sie die URL und versuchen Sie es erneut.",
        }[language],
    }


def _hub_keyboard(language: str) -> InlineKeyboardMarkup:
    labels = {
        "ar": ("🎬 تحميل رابط", "📦 تحميل عدة روابط", "📚 سجل التنزيلات", "ℹ️ معلومات الرابط"),
        "en": ("🎬 Download a link", "📦 Batch download", "📚 Download history", "ℹ️ Link information"),
        "tr": ("🎬 Bağlantı indir", "📦 Toplu indirme", "📚 İndirme geçmişi", "ℹ️ Bağlantı bilgisi"),
        "de": ("🎬 Link herunterladen", "📦 Stapel-Download", "📚 Download-Verlauf", "ℹ️ Linkinformationen"),
    }[language]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels[0], callback_data="user_single_download")],
        [InlineKeyboardButton(labels[1], callback_data="user_batch_prompt")],
        [InlineKeyboardButton(labels[2], callback_data="user_history")],
        [InlineKeyboardButton(labels[3], callback_data="user_link_info")],
    ])


def _type_keyboard(language: str) -> InlineKeyboardMarkup:
    labels = {
        "ar": ("🎥 تحميل فيديو", "🎵 تحميل صوت"),
        "en": ("🎥 Download Video", "🎵 Download Audio"),
        "tr": ("🎥 Video indir", "🎵 Ses indir"),
        "de": ("🎥 Video herunterladen", "🎵 Audio herunterladen"),
    }[language]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(labels[0], callback_data="video_menu")],
        [InlineKeyboardButton(labels[1], callback_data="audio_menu")],
    ])


def _extract_urls(text: str) -> list[str]:
    found: list[str] = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(".,;!?)[]}>")
        if len(url) > MAX_URL_LENGTH:
            continue
        if url not in found:
            found.append(url)
    return found


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _format_duration(value: Any) -> str:
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_views(value: Any) -> str:
    try:
        views = int(value)
    except (TypeError, ValueError):
        return ""
    if views < 0:
        return ""
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)


def _extract_info_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        "title": _clean_text(payload.get("title")),
        "uploader": _clean_text(payload.get("uploader") or payload.get("channel")),
        "duration": _format_duration(payload.get("duration")),
        "views": _format_views(payload.get("view_count")),
        "upload_date": _clean_text(payload.get("upload_date")),
        "webpage_url": _clean_text(payload.get("webpage_url")),
    }


def _info_command(url: str) -> list[str]:
    return [
        "python",
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--skip-download",
        "--dump-single-json",
        "--no-warnings",
        "--socket-timeout",
        "15",
        url,
    ]


async def _fetch_link_info(url: str) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        *_info_command(url),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=INFO_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise RuntimeError("info_timeout")

    if process.returncode != 0 or not stdout:
        raise RuntimeError(
            (stderr or b"").decode("utf-8", "ignore")[-800:]
            or "info_failed"
        )

    if len(stdout) > INFO_MAX_OUTPUT_BYTES:
        raise RuntimeError("info_output_too_large")

    try:
        payload = json.loads(stdout.decode("utf-8", "ignore"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("info_invalid_json") from exc

    info = _extract_info_payload(payload)
    if not info.get("title"):
        raise RuntimeError("info_missing_title")
    return info


async def _show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user or user.id == getattr(bot_module, "ADMIN_ID", -1):
        if query:
            await query.answer()
        return
    await query.answer()
    if bot_module.is_banned(user.id):
        await query.message.reply_text(bot_module.TEXTS["ar"]["banned"])
        return
    language = _language(bot_module, user.id)
    await query.edit_message_text(_messages(language)["hub"], parse_mode="HTML", reply_markup=_hub_keyboard(language))


async def _single_download(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    if not user or bot_module.is_banned(user.id):
        return
    language = _language(bot_module, user.id)
    await query.edit_message_text(_messages(language)["single"])


async def _batch_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    if not user or bot_module.is_banned(user.id):
        return
    language = _language(bot_module, user.id)
    context.user_data["user_batch_waiting"] = True
    await query.edit_message_text(_messages(language)["batch_prompt"], parse_mode="HTML")


async def _info_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()
    if bot_module.is_banned(user.id):
        return
    language = _language(bot_module, user.id)
    await query.edit_message_text(
        _messages(language)["info_prompt"],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 رجوع" if language == "ar" else "🔙 Back", callback_data="start_button")]
        ]),
    )


async def _info_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message:
        return
    bot_module.register_user(user)
    if bot_module.is_banned(user.id):
        await message.reply_text(bot_module.TEXTS["ar"]["banned"])
        return
    language = _language(bot_module, user.id)
    if not bot_module.get_language(user.id):
        await message.reply_text(
            bot_module.TEXTS["ar"]["choose_language"],
            reply_markup=bot_module.language_keyboard(),
        )
        return
    if not context.args or len(context.args) != 1:
        await message.reply_text(_messages(language)["info_usage"], parse_mode="HTML")
        return

    url = context.args[0].strip()
    if len(url) > MAX_URL_LENGTH:
        await message.reply_text(_messages(language)["info_failed"])
        return
    try:
        bot_module.validate_public_http_url(url)
    except Exception:
        await message.reply_text(bot_module.TEXTS[language]["invalid_url"])
        return

    status = await message.reply_text(_messages(language)["info_processing"])
    try:
        info = await _fetch_link_info(url)
    except Exception as exc:
        print(f"User link info failed: {type(exc).__name__}", flush=True)
        await status.edit_text(_messages(language)["info_failed"])
        return

    labels = {
        "ar": ("ℹ️ <b>معلومات الرابط</b>", "🎬 العنوان", "👤 الناشر", "⏱ المدة", "👁 المشاهدات", "📅 التاريخ"),
        "en": ("ℹ️ <b>Link information</b>", "🎬 Title", "👤 Uploader", "⏱ Duration", "👁 Views", "📅 Date"),
        "tr": ("ℹ️ <b>Bağlantı bilgisi</b>", "🎬 Başlık", "👤 Yükleyen", "⏱ Süre", "👁 Görüntülenme", "📅 Tarih"),
        "de": ("ℹ️ <b>Linkinformationen</b>", "🎬 Titel", "👤 Uploader", "⏱ Dauer", "👁 Aufrufe", "📅 Datum"),
    }[language]
    title_label, uploader_label, duration_label, views_label, date_label = labels[1:]
    lines = [labels[0], "━━━━━━━━━━━━━━━━━━", "", f"{title_label}: {html_escape(info['title'])}"]
    if info.get("uploader"):
        lines.append(f"{uploader_label}: {html_escape(info['uploader'])}")
    if info.get("duration"):
        lines.append(f"{duration_label}: {info['duration']}")
    if info.get("views"):
        lines.append(f"{views_label}: {info['views']}")
    if info.get("upload_date"):
        lines.append(f"{date_label}: {info['upload_date']}")
    lines.append("\n👇 اختر ما تريد فعله بالرابط." if language == "ar" else "\n👇 Choose what to do with this link.")

    context.user_data["video_url"] = url
    await status.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_type_keyboard(language),
    )


def html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


async def _history(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    user = update.effective_user
    if not user or bot_module.is_banned(user.id):
        return
    language = _language(bot_module, user.id)
    conn = bot_module.get_db()
    try:
        rows = conn.execute(
            "SELECT id, url, website, media_type, quality, created_at FROM downloads WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (user.id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        target = getattr(update, "message", None)
        if target:
            await target.reply_text(_messages(language)["history_empty"], parse_mode="HTML")
        return
    context.user_data["user_history"] = [dict(row) for row in rows]
    text = _messages(language)["history_title"]
    keyboard = []
    for index, row in enumerate(rows):
        website = str(row["website"] or "Other")[:22]
        media = "🎵" if str(row["media_type"]).lower() == "audio" else "🎥"
        quality = str(row["quality"] or "")[:18]
        label = f"{index + 1}️⃣ {media} {website} {quality}".strip()
        text += f"{index + 1}. {website} • {quality}\n"
        keyboard.append([InlineKeyboardButton(label[:60], callback_data=f"user_history_pick_{index}")])
    target = getattr(update, "message", None)
    if target:
        await target.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def _history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    if not user or bot_module.is_banned(user.id):
        return
    match = HISTORY_RE.match(query.data or "")
    if not match:
        return
    rows = context.user_data.get("user_history") or []
    index = int(match.group(1))
    if index < 0 or index >= len(rows):
        await query.edit_message_text("❌ انتهت صلاحية السجل. أرسل /history من جديد.")
        return
    selected = rows[index]
    try:
        bot_module.validate_public_http_url(selected["url"])
    except Exception:
        await query.edit_message_text("❌ تعذر التحقق من الرابط المحفوظ.")
        return
    context.user_data["video_url"] = selected["url"]
    language = _language(bot_module, user.id)
    await query.edit_message_text(
        f"🔁 <b>إعادة تحميل</b>\n\n{selected['website']} • {selected['quality']}",
        parse_mode="HTML",
        reply_markup=_type_keyboard(language),
    )


async def _batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    if not update.message or not update.effective_user:
        return
    if not context.user_data.get("user_batch_waiting"):
        return
    user = update.effective_user
    language = _language(bot_module, user.id)
    urls = _extract_urls(update.message.text or "")
    if len(urls) < 2 or len(urls) > MAX_BATCH_URLS:
        await update.message.reply_text(_messages(language)["invalid_batch"], parse_mode="HTML")
        raise ApplicationHandlerStop
    valid: list[str] = []
    for url in urls:
        try:
            bot_module.validate_public_http_url(url)
        except Exception:
            continue
        valid.append(url)
    if len(valid) < 2:
        await update.message.reply_text(_messages(language)["invalid_batch"], parse_mode="HTML")
        raise ApplicationHandlerStop
    context.user_data["user_batch_waiting"] = False
    context.user_data["user_batch_urls"] = valid
    await update.message.reply_text(_messages(language)["batch_ready"].format(count=len(valid)), parse_mode="HTML", reply_markup=_type_keyboard(language))
    raise ApplicationHandlerStop


class _QueryProxy:
    """Forward Telegram callback operations but keep the source message alive."""
    def __init__(self, query):
        self._query = query
        self.data = query.data
        self.from_user = query.from_user
        self.message = query.message

    async def answer(self, *args, **kwargs):
        return await self._query.answer(*args, **kwargs)

    async def edit_message_text(self, *args, **kwargs):
        return await self._query.edit_message_text(*args, **kwargs)

    async def delete_message(self, *args, **kwargs):
        return None


async def _batch_type_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not context.user_data.get("user_batch_urls"):
        return
    if not BATCH_TYPE_RE.match(query.data or ""):
        return
    context.user_data["video_url"] = list(context.user_data["user_batch_urls"])[0]
    await bot_module.download_media(update, context)
    raise ApplicationHandlerStop


async def _batch_quality(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    if not context.user_data.get("user_batch_urls"):
        return
    if not BATCH_QUALITY_RE.match(query.data or ""):
        return
    urls = list(context.user_data.get("user_batch_urls") or [])
    language = _language(bot_module, user.id)
    lock = _lock_for(user.id)
    if lock.locked():
        await query.answer()
        await query.edit_message_text(_messages(language)["batch_busy"])
        return
    async with lock:
        proxy = _QueryProxy(query)
        batch_update = SimpleNamespace(
            callback_query=proxy,
            effective_user=update.effective_user,
            effective_chat=update.effective_chat,
        )
        for index, url in enumerate(urls, start=1):
            context.user_data["video_url"] = url
            context.user_data["batch_index"] = index
            try:
                await bot_module.download_media(batch_update, context)
            except Exception:
                continue
        context.user_data.pop("user_batch_urls", None)
        context.user_data.pop("batch_index", None)
        try:
            await query.edit_message_text(_messages(language)["batch_done"].format(count=len(urls)), parse_mode="HTML")
        except Exception:
            pass
    raise ApplicationHandlerStop


async def _user_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    if not user or bot_module.is_banned(user.id):
        return
    await _history(SimpleNamespace(message=query.message, effective_user=user), context, bot_module)


def register_user_features(app: Any, bot_module: Any) -> None:
    """Register user features ahead of legacy handlers without replacing them."""
    app.add_handler(CallbackQueryHandler(lambda u, c: _show_hub(u, c, bot_module), pattern=r"^start_button$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: _single_download(u, c, bot_module), pattern=r"^user_single_download$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: _batch_prompt(u, c, bot_module), pattern=r"^user_batch_prompt$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: _user_history_callback(u, c, bot_module), pattern=r"^user_history$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: _info_prompt(u, c, bot_module), pattern=r"^user_link_info$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: _history_callback(u, c, bot_module), pattern=r"^user_history_pick_\d+$"), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: _batch_type_menu(u, c, bot_module), pattern=BATCH_TYPE_RE), group=-1)
    app.add_handler(CallbackQueryHandler(lambda u, c: _batch_quality(u, c, bot_module), pattern=BATCH_QUALITY_RE), group=-1)
    app.add_handler(CommandHandler("history", lambda u, c: _history(u, c, bot_module)), group=-1)
    app.add_handler(CommandHandler("batch", lambda u, c: _batch_prompt_command(u, c, bot_module)), group=-1)
    app.add_handler(CommandHandler("info", lambda u, c: _info_command(u, c, bot_module)), group=-1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: _batch_message(u, c, bot_module)), group=-1)
    print("👤 User features layer: ENABLED (history + bounded batch + link info)", flush=True)


async def _batch_prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    user = update.effective_user
    if not user or bot_module.is_banned(user.id):
        return
    language = _language(bot_module, user.id)
    context.user_data["user_batch_waiting"] = True
    await update.message.reply_text(_messages(language)["batch_prompt"], parse_mode="HTML")
