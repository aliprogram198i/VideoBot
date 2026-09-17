"""Safe additive UX enhancements for AliBot.

Owns only new callback/message surfaces at an earlier handler group:
- paginated/searchable library
- real task cancellation for active downloads
- rich-media administrative broadcasts
"""
from __future__ import annotations

import asyncio
import functools
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import RetryAfter
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

PAGE_SIZE = 8


def _admin(update: Update, bot_module: Any) -> bool:
    return bool(update.effective_user and update.effective_user.id == getattr(bot_module, "ADMIN_ID", None))


def _db(bot_module: Any):
    return bot_module.get_db()


def _lang(bot_module, user_id: int) -> str:
    return bot_module.get_language(user_id) or "ar"


def _labels(lang: str) -> dict[str, str]:
    return {
        "ar": {"title":"📚 <b>مكتبتي</b>","empty":"لا توجد عناصر محفوظة.","search":"🔎 بحث","prev":"⬅️ السابق","next":"التالي ➡️","back":"🔙 رجوع","search_prompt":"أرسل كلمة البحث داخل مكتبتك، أو /cancel للإلغاء.","clear_search":"✖️ مسح البحث","saved":"محفوظ","load":"▶️","delete":"🗑️","page":"صفحة","cancelled":"✅ تم إلغاء العملية وإيقاف التحميل الجاري.","no_active":"ℹ️ لا يوجد تحميل نشط لإلغائه.","broadcast_prompt":"📢 أرسل الآن الإعلان: نص، صورة، فيديو، صوت، رسالة صوتية أو ملف.\n\nثم ستظهر لك معاينة قبل الإرسال.","broadcast_preview":"👀 معاينة الإعلان\n\nاضغط إرسال للجميع للتأكيد.","send":"📤 إرسال للجميع","abort":"❌ إلغاء","done":"✅ انتهى الإرسال","targets":"المستهدفون","sent":"تم الإرسال","failed":"فشل","unsupported":"❌ نوع الرسالة غير مدعوم للإعلان."},
        "en": {"title":"📚 <b>My library</b>","empty":"No saved items.","search":"🔎 Search","prev":"⬅️ Previous","next":"Next ➡️","back":"🔙 Back","search_prompt":"Send a search term for your library, or /cancel to exit.","clear_search":"✖️ Clear search","saved":"Saved","load":"▶️","delete":"🗑️","page":"Page","cancelled":"✅ Operation cancelled and the active download was stopped.","no_active":"ℹ️ No active download to cancel.","broadcast_prompt":"📢 Send the announcement now: text, photo, video, audio, voice message, or document.\n\nYou will get a preview before delivery.","broadcast_preview":"👀 Announcement preview\n\nPress Send to all to confirm.","send":"📤 Send to all","abort":"❌ Cancel","done":"✅ Broadcast finished","targets":"Targets","sent":"Sent","failed":"Failed","unsupported":"❌ This message type is not supported for broadcast."},
        "tr": {"title":"📚 <b>Kitaplığım</b>","empty":"Kayıtlı öğe yok.","search":"🔎 Ara","prev":"⬅️ Önceki","next":"Sonraki ➡️","back":"🔙 Geri","search_prompt":"Kitaplığınızda aramak için bir kelime gönderin veya /cancel yazın.","clear_search":"✖️ Aramayı temizle","saved":"Kayıtlı","load":"▶️","delete":"🗑️","page":"Sayfa","cancelled":"✅ İşlem iptal edildi ve aktif indirme durduruldu.","no_active":"ℹ️ İptal edilecek aktif indirme yok.","broadcast_prompt":"📢 Duyuruyu gönderin: metin, fotoğraf, video, ses, sesli mesaj veya dosya.\n\nGönderimden önce önizleme gösterilir.","broadcast_preview":"👀 Duyuru önizlemesi\n\nOnaylamak için herkese gönder'e basın.","send":"📤 Herkese gönder","abort":"❌ İptal","done":"✅ Duyuru tamamlandı","targets":"Hedef","sent":"Gönderildi","failed":"Başarısız","unsupported":"❌ Bu mesaj türü desteklenmiyor."},
        "de": {"title":"📚 <b>Meine Bibliothek</b>","empty":"Keine gespeicherten Elemente.","search":"🔎 Suchen","prev":"⬅️ Zurück","next":"Weiter ➡️","back":"🔙 Zurück","search_prompt":"Suchbegriff senden oder /cancel zum Beenden.","clear_search":"✖️ Suche löschen","saved":"Gespeichert","load":"▶️","delete":"🗑️","page":"Seite","cancelled":"✅ Vorgang abgebrochen und aktiver Download gestoppt.","no_active":"ℹ️ Kein aktiver Download zum Abbrechen.","broadcast_prompt":"📢 Senden Sie die Ankündigung: Text, Foto, Video, Audio, Sprachnachricht oder Datei.\n\nVor dem Versand wird eine Vorschau angezeigt.","broadcast_preview":"👀 Ankündigungsvorschau\n\nZum Bestätigen auf An alle senden drücken.","send":"📤 An alle senden","abort":"❌ Abbrechen","done":"✅ Broadcast abgeschlossen","targets":"Ziele","sent":"Gesendet","failed":"Fehlgeschlagen","unsupported":"❌ Dieser Nachrichtentyp wird nicht unterstützt."},
    }.get(lang, {})


def _ensure_tables(bot_module):
    conn = _db(bot_module)
    conn.execute("""CREATE TABLE IF NOT EXISTS broadcast_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, broadcast_id INTEGER NOT NULL, user_id INTEGER NOT NULL, message_id INTEGER NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_messages_broadcast_id ON broadcast_messages(broadcast_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_messages_user_id ON broadcast_messages(user_id)")
    conn.commit()
    conn.close()


async def _library_render(target, context, bot_module, user_id: int, page: int = 0, query_text: str = ""):
    msg = _labels(_lang(bot_module, user_id))
    query_text = (query_text or "").strip()
    conn = _db(bot_module)
    try:
        where = "WHERE user_id = ?"
        params: list[Any] = [user_id]
        if query_text:
            where += " AND (title LIKE ? OR website LIKE ? OR url LIKE ?)"
            needle = f"%{query_text}%"
            params.extend([needle, needle, needle])
        total = int(conn.execute(f"SELECT COUNT(*) FROM user_favorites {where}", params).fetchone()[0])
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        rows = conn.execute(
            f"SELECT id, website, title, media_type, quality FROM user_favorites {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [PAGE_SIZE, page * PAGE_SIZE],
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        text = f"{msg['title']}\n\n{msg['empty']}"
        if query_text:
            text += f"\n\n🔎 {query_text}"
        keyboard = [[InlineKeyboardButton(msg["search"], callback_data="ux_lib_search")],[InlineKeyboardButton(msg["back"], callback_data="main_menu")]]
    else:
        lines = [msg["title"], "━━━━━━━━━━━━━━━━━━", ""]
        keyboard = []
        for index, row in enumerate(rows, page * PAGE_SIZE + 1):
            title = " ".join(str(row["title"] or row["website"] or msg["saved"]).split())[:42]
            kind = "🎵" if row["media_type"] == "audio" else "🎥"
            quality = f" · {row['quality']}" if row["quality"] else ""
            lines.append(f"{index}. {title} {kind}{quality}")
            keyboard.append([InlineKeyboardButton(f"{msg['load']} {index} {title}"[:58], callback_data=f"ux_load_{row['id']}"), InlineKeyboardButton(msg["delete"], callback_data=f"ux_fav_{row['id']}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(msg["prev"], callback_data=f"ux_lib_page_{page-1}"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(msg["next"], callback_data=f"ux_lib_page_{page+1}"))
        if nav:
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton(msg["search"], callback_data="ux_lib_search"), InlineKeyboardButton(msg["clear_search"], callback_data="ux_lib_clear")])
        keyboard.append([InlineKeyboardButton(f"{msg['page']} {page+1}/{pages}", callback_data="ux_lib_noop"), InlineKeyboardButton(msg["back"], callback_data="main_menu")])
        text = "\n".join(lines)
        if query_text:
            text += f"\n\n🔎 {query_text}"
    try:
        await target.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await target.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def _library_command(update, context):
    bot_module = __import__("bot")
    user = update.effective_user
    if not user or bot_module.is_banned(user.id):
        return
    context.user_data["library_page"] = 0
    context.user_data["library_query"] = ""
    await _library_render(update.message, context, bot_module, user.id)
    raise ApplicationHandlerStop


async def _library_callback(update, context):
    query = update.callback_query
    user = update.effective_user
    bot_module = __import__("bot")
    if not user or bot_module.is_banned(user.id):
        await query.answer()
        raise ApplicationHandlerStop
    data = query.data or ""
    await query.answer()
    page = int(context.user_data.get("library_page", 0))
    search = str(context.user_data.get("library_query", ""))
    if data == "ux_library":
        page = 0
    elif data.startswith("ux_lib_page_"):
        page = int(data.rsplit("_", 1)[1])
    elif data == "ux_lib_search":
        context.user_data["library_searching"] = True
        await query.message.reply_text(_labels(_lang(bot_module, user.id))["search_prompt"])
        raise ApplicationHandlerStop
    elif data == "ux_lib_clear":
        search = ""; page = 0; context.user_data["library_query"] = ""
    elif data == "ux_lib_noop":
        raise ApplicationHandlerStop
    elif data.startswith("ux_fav_"):
        favorite_id = int(data.split("_", 2)[2])
        conn = _db(bot_module)
        try:
            conn.execute("DELETE FROM user_favorites WHERE id=? AND user_id=?", (favorite_id, user.id)); conn.commit()
        finally: conn.close()
    elif data.startswith("ux_load_"):
        favorite_id = int(data.split("_", 2)[2])
        conn = _db(bot_module)
        try:
            row = conn.execute("SELECT url, media_type, quality FROM user_favorites WHERE id=? AND user_id=?", (favorite_id, user.id)).fetchone()
        finally: conn.close()
        msg = _labels(_lang(bot_module, user.id))
        if not row:
            await query.edit_message_text("❌ " + msg["empty"]); raise ApplicationHandlerStop
        try:
            bot_module.validate_public_http_url(str(row["url"]))
        except Exception:
            await query.edit_message_text("❌ " + msg["empty"]); raise ApplicationHandlerStop
        context.user_data["video_url"] = str(row["url"])
        saved_type, saved_quality = str(row["media_type"] or ""), str(row["quality"] or "")
        if saved_type in {"video", "audio"} and saved_quality:
            context.user_data["ux_saved_preference"] = saved_quality
        else:
            context.user_data.pop("ux_saved_preference", None)
        await query.edit_message_text("🔁 <b>إعادة تحميل من المكتبة</b>\n\nاختر النوع أو استخدم الإعداد المحفوظ:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎥 فيديو", callback_data="video_menu")],[InlineKeyboardButton("🎵 MP3", callback_data="audio_menu")],[InlineKeyboardButton("⚙️ استخدام الإعدادات المحفوظة", callback_data="ux_use_preferences")]]))
        raise ApplicationHandlerStop
    context.user_data["library_page"] = page
    context.user_data["library_query"] = search
    await _library_render(query, context, bot_module, user.id, page, search)
    raise ApplicationHandlerStop


async def _library_search_message(update, context):
    if not context.user_data.get("library_searching") or not update.message or not update.effective_user:
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    bot_module = __import__("bot")
    if bot_module.is_banned(update.effective_user.id):
        context.user_data.pop("library_searching", None); return
    if text.lower() == "/cancel":
        context.user_data.pop("library_searching", None)
        await update.message.reply_text(_labels(_lang(bot_module, update.effective_user.id))["back"])
        raise ApplicationHandlerStop
    context.user_data.pop("library_searching", None)
    context.user_data["library_query"] = text[:80]
    context.user_data["library_page"] = 0
    await _library_render(update.message, context, bot_module, update.effective_user.id, 0, text[:80])
    raise ApplicationHandlerStop


async def _cancel_callback(update, context):
    query = update.callback_query
    bot_module = __import__("bot")
    user = update.effective_user
    await query.answer()
    task = context.user_data.pop("active_download_task", None)
    labels = _labels(_lang(bot_module, user.id))
    if task and not task.done():
        task.cancel()
        await asyncio.sleep(0)
        message = labels["cancelled"]
    else:
        message = labels["no_active"]
    try:
        context.user_data.pop("video_url", None)
        context.user_data.pop("sdc_info", None)
        await query.edit_message_text(message)
    except Exception:
        pass
    raise ApplicationHandlerStop


async def _noop_callback(update, context):
    await update.callback_query.answer()
    raise ApplicationHandlerStop


def _install_download_task_tracking(bot_module):
    if getattr(bot_module, "_alibot_download_tracking_installed", False):
        return
    original = bot_module.download_media
    @functools.wraps(original)
    async def tracked(update, context):
        task = asyncio.current_task()
        context.user_data["active_download_task"] = task
        try:
            return await original(update, context)
        finally:
            if context.user_data.get("active_download_task") is task:
                context.user_data.pop("active_download_task", None)
    bot_module.download_media = tracked
    bot_module._alibot_download_tracking_installed = True


async def _broadcast_start(update, context):
    bot_module = __import__("bot")
    if not _admin(update, bot_module):
        return
    context.user_data["rich_broadcast_waiting"] = True
    context.user_data.pop("rich_broadcast_payload", None)
    await update.effective_message.reply_text(_labels("ar")["broadcast_prompt"])
    raise ApplicationHandlerStop


def _extract_payload(message):
    if message.text and not message.text.startswith("/"):
        return {"kind":"text","chat_id":message.chat_id,"message_id":message.message_id,"preview":message.text[:4000]}
    if message.photo:
        return {"kind":"photo","chat_id":message.chat_id,"message_id":message.message_id,"preview":message.caption or "(photo)"}
    if message.video:
        return {"kind":"video","chat_id":message.chat_id,"message_id":message.message_id,"preview":message.caption or "(video)"}
    if message.audio:
        return {"kind":"audio","chat_id":message.chat_id,"message_id":message.message_id,"preview":message.caption or "(audio)"}
    if message.voice:
        return {"kind":"voice","chat_id":message.chat_id,"message_id":message.message_id,"preview":message.caption or "(voice)"}
    if message.document:
        return {"kind":"document","chat_id":message.chat_id,"message_id":message.message_id,"preview":message.caption or "(document)"}
    return None


async def _broadcast_capture(update, context):
    if not context.user_data.get("rich_broadcast_waiting") or not update.message:
        return
    bot_module = __import__("bot")
    if not _admin(update, bot_module):
        return
    payload = _extract_payload(update.message)
    msg = _labels("ar")
    if not payload:
        await update.message.reply_text(msg["unsupported"])
        raise ApplicationHandlerStop
    context.user_data["rich_broadcast_waiting"] = False
    context.user_data["rich_broadcast_payload"] = payload
    await update.message.reply_text(
        f"{msg['broadcast_preview']}\n\n📦 النوع: {payload['kind']}\n📝 {payload['preview']}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(msg["send"], callback_data="admin_rich_broadcast_confirm")],[InlineKeyboardButton(msg["abort"], callback_data="admin_rich_broadcast_cancel")]]),
    )
    raise ApplicationHandlerStop


async def _broadcast_cancel(update, context):
    await update.callback_query.answer()
    context.user_data.pop("rich_broadcast_waiting", None)
    context.user_data.pop("rich_broadcast_payload", None)
    await update.callback_query.edit_message_text("❌ تم إلغاء الإعلان.")
    raise ApplicationHandlerStop


async def _broadcast_confirm(update, context):
    query = update.callback_query
    await query.answer()
    bot_module = __import__("bot")
    if not _admin(update, bot_module):
        return
    payload = context.user_data.pop("rich_broadcast_payload", None)
    if not payload:
        await query.edit_message_text("❌ انتهت صلاحية الإعلان."); raise ApplicationHandlerStop
    _ensure_tables(bot_module)
    conn = _db(bot_module)
    try:
        users = conn.execute("SELECT user_id FROM users WHERE is_banned=0").fetchall()
        cur = conn.cursor()
        cur.execute("INSERT INTO broadcast_logs(admin_id,message,sent_count,failed_count,created_at) VALUES(?,?,?,?,?)", (bot_module.ADMIN_ID, f"[rich:{payload['kind']}] {payload.get('preview','')}",0,0,datetime.now().isoformat()))
        broadcast_id = cur.lastrowid
        conn.commit()
    finally: conn.close()
    msg = _labels("ar")
    status = await query.edit_message_text(f"📢 جاري إرسال الإعلان...\n\n👥 المستهدفون: {len(users)}")
    sent = failed = 0
    for row in users:
        try:
            if payload["kind"] == "text":
                result = await context.bot.send_message(chat_id=row["user_id"], text=payload["preview"])
            else:
                result = await context.bot.copy_message(chat_id=row["user_id"], from_chat_id=payload["chat_id"], message_id=payload["message_id"])
            message_id = getattr(result, "message_id", None)
            if message_id is not None:
                conn = _db(bot_module)
                try:
                    conn.execute("INSERT INTO broadcast_messages(broadcast_id,user_id,message_id,created_at) VALUES(?,?,?,?)", (broadcast_id,row["user_id"],message_id,datetime.now().isoformat())); conn.commit()
                finally: conn.close()
            sent += 1
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.25)
            try:
                if payload["kind"] == "text":
                    result = await context.bot.send_message(chat_id=row["user_id"], text=payload["preview"])
                else:
                    result = await context.bot.copy_message(chat_id=row["user_id"], from_chat_id=payload["chat_id"], message_id=payload["message_id"])
                message_id = getattr(result, "message_id", None)
                if message_id is not None:
                    conn = _db(bot_module)
                    try:
                        conn.execute("INSERT INTO broadcast_messages(broadcast_id,user_id,message_id,created_at) VALUES(?,?,?,?)", (broadcast_id,row["user_id"],message_id,datetime.now().isoformat())); conn.commit()
                    finally: conn.close()
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    conn = _db(bot_module)
    try:
        conn.execute("UPDATE broadcast_logs SET sent_count=?, failed_count=? WHERE id=?",(sent,failed,broadcast_id)); conn.commit()
    finally: conn.close()
    await status.edit_text(f"{msg['done']}\n\n📨 {msg['sent']}: {sent}\n❌ {msg['failed']}: {failed}\n👥 {msg['targets']}: {len(users)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 إعلان جديد",callback_data="admin_broadcast")],[InlineKeyboardButton("🎛️ مركز التحكم",callback_data="admin_control_center")]]))
    raise ApplicationHandlerStop


def register(app, bot_module):
    if getattr(app, "_alibot_enhancements_registered", False):
        return
    app._alibot_enhancements_registered = True
    _install_download_task_tracking(bot_module)
    app.add_handler(CommandHandler("library", _library_command), group=-4)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _library_search_message), group=-4)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, _broadcast_capture), group=-4)
    app.add_handler(CommandHandler("broadcast", _broadcast_start), group=-4)
    app.add_handler(CallbackQueryHandler(_cancel_callback, pattern=r"^sdc_cancel$"), group=-4)
    app.add_handler(CallbackQueryHandler(_library_callback, pattern=r"^(ux_library|ux_lib_(?:page_\d+|search|clear|noop)|ux_fav_\d+|ux_load_\d+)$"), group=-4)
    app.add_handler(CallbackQueryHandler(_broadcast_confirm, pattern=r"^admin_rich_broadcast_confirm$"), group=-4)
    app.add_handler(CallbackQueryHandler(_broadcast_cancel, pattern=r"^admin_rich_broadcast_cancel$"), group=-4)
    print("🧩 UX enhancements: library paging/search + active cancellation + rich broadcasts enabled", flush=True)
