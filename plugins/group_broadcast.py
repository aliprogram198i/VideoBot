"""Production-safe scheduled group broadcast controls.

The extension is isolated from the existing user/download tables. It stores only
its own group/campaign metadata and runs one scheduler inside the existing bot
polling process (never a second Telegram polling process).
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

TABLE_GROUPS = "group_broadcast_groups"
TABLE_CAMPAIGN = "group_broadcast_campaign"
STATE = "group_broadcast_state"
INTERVAL_RE = re.compile(r"^\s*(\d+)\s*(m|h|d)\s*$", re.IGNORECASE)

INTERVALS = {
    1 * 3600: "كل ساعة",
    6 * 3600: "كل 6 ساعات",
    12 * 3600: "كل 12 ساعة",
    1 * 86400: "كل يوم",
    3 * 86400: "كل 3 أيام",
    5 * 86400: "كل 5 أيام",
    7 * 86400: "كل 7 أيام",
    14 * 86400: "كل 14 يومًا",
    30 * 86400: "كل 30 يومًا",
}
DURATIONS = {
    "forever": (None, "بدون انتهاء"),
    "1d": (1 * 86400, "يوم واحد"),
    "7d": (7 * 86400, "7 أيام"),
    "30d": (30 * 86400, "30 يومًا"),
    "90d": (90 * 86400, "90 يومًا"),
}


def _now() -> datetime:
    return datetime.now()


def _format_interval(seconds: int) -> str:
    if seconds in INTERVALS:
        return INTERVALS[seconds]
    if seconds % 86400 == 0:
        return f"كل {seconds // 86400} يوم"
    if seconds % 3600 == 0:
        return f"كل {seconds // 3600} ساعة"
    return f"كل {seconds // 60} دقيقة"


def _parse_interval(value: str) -> int | None:
    match = INTERVAL_RE.match(value or "")
    if not match:
        return None
    amount = int(match.group(1))
    if amount <= 0:
        return None
    unit = match.group(2).lower()
    seconds = amount * {"m": 60, "h": 3600, "d": 86400}[unit]
    if seconds < 300 or seconds > 365 * 86400:
        return None
    return seconds


def _ensure_column(conn, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_tables(bot_module: Any) -> None:
    conn = bot_module.get_db()
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_GROUPS} (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_CAMPAIGN} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                interval_seconds INTEGER NOT NULL DEFAULT 432000,
                content_type TEXT,
                text TEXT,
                file_id TEXT,
                caption TEXT,
                next_send_at TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        _ensure_column(conn, TABLE_CAMPAIGN, "expires_at", "TEXT")
        _ensure_column(conn, TABLE_CAMPAIGN, "last_sent_at", "TEXT")
        _ensure_column(conn, TABLE_CAMPAIGN, "last_sent_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, TABLE_CAMPAIGN, "last_failed_count", "INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def _extract_content(message):
    if message.text:
        return {"content_type": "text", "text": message.text, "file_id": None, "caption": None}
    if message.photo:
        return {"content_type": "photo", "text": None, "file_id": message.photo[-1].file_id, "caption": message.caption or ""}
    if message.video:
        return {"content_type": "video", "text": None, "file_id": message.video.file_id, "caption": message.caption or ""}
    if message.audio:
        return {"content_type": "audio", "text": None, "file_id": message.audio.file_id, "caption": message.caption or ""}
    if message.voice:
        return {"content_type": "voice", "text": None, "file_id": message.voice.file_id, "caption": message.caption or ""}
    return None


async def _send_template(bot, chat_id: int, campaign) -> None:
    kind = campaign["content_type"]
    if kind == "text":
        await bot.send_message(chat_id=chat_id, text=campaign["text"])
    elif kind == "photo":
        await bot.send_photo(chat_id=chat_id, photo=campaign["file_id"], caption=campaign["caption"] or None)
    elif kind == "video":
        await bot.send_video(chat_id=chat_id, video=campaign["file_id"], caption=campaign["caption"] or None)
    elif kind == "audio":
        await bot.send_audio(chat_id=chat_id, audio=campaign["file_id"], caption=campaign["caption"] or None)
    elif kind == "voice":
        await bot.send_voice(chat_id=chat_id, voice=campaign["file_id"], caption=campaign["caption"] or None)


async def _send_to_active_groups(bot, bot_module: Any) -> tuple[int, int]:
    _ensure_tables(bot_module)
    conn = bot_module.get_db()
    try:
        groups = conn.execute(f"SELECT chat_id FROM {TABLE_GROUPS} WHERE enabled = 1 ORDER BY chat_id").fetchall()
        campaign = conn.execute(f"SELECT * FROM {TABLE_CAMPAIGN} WHERE id = 1").fetchone()
    finally:
        conn.close()
    if not campaign or not campaign["content_type"]:
        return 0, 0
    sent = failed = 0
    for group in groups:
        try:
            await _send_template(bot, group["chat_id"], campaign)
            sent += 1
        except Exception as exc:
            failed += 1
            print(f"Group broadcast error {group['chat_id']}: {exc}", flush=True)
        await asyncio.sleep(1.0)
    return sent, failed


async def _scheduler_tick(bot, bot_module: Any) -> None:
    _ensure_tables(bot_module)
    conn = bot_module.get_db()
    try:
        row = conn.execute(f"SELECT * FROM {TABLE_CAMPAIGN} WHERE id = 1 AND enabled = 1").fetchone()
    finally:
        conn.close()
    if not row or not row["content_type"] or not row["next_send_at"]:
        return
    now = _now()
    if row["expires_at"]:
        try:
            if now >= datetime.fromisoformat(row["expires_at"]):
                conn = bot_module.get_db()
                try:
                    conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET enabled=0, updated_at=? WHERE id=1", (now.isoformat(),))
                    conn.commit()
                finally:
                    conn.close()
                print("📢 Group broadcast campaign expired and was stopped.", flush=True)
                return
        except ValueError:
            pass
    try:
        due_at = datetime.fromisoformat(row["next_send_at"])
    except ValueError:
        due_at = now
    if now < due_at:
        return
    sent, failed = await _send_to_active_groups(bot, bot_module)
    next_send = _now() + timedelta(seconds=int(row["interval_seconds"]))
    conn = bot_module.get_db()
    try:
        conn.execute(f"""
            UPDATE {TABLE_CAMPAIGN}
            SET next_send_at=?, last_sent_at=?, last_sent_count=?, last_failed_count=?, updated_at=?
            WHERE id=1
        """, (next_send.isoformat(), _now().isoformat(), sent, failed, _now().isoformat()))
        conn.commit()
    finally:
        conn.close()
    print(f"📢 Group broadcast cycle finished: sent={sent}, failed={failed}, next={next_send.isoformat()}", flush=True)


async def _scheduler_loop(application, bot_module: Any) -> None:
    while True:
        try:
            await _scheduler_tick(application.bot, bot_module)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Group broadcast scheduler error: {exc}", flush=True)
        await asyncio.sleep(60)


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 المحتوى", callback_data="gb:content"), InlineKeyboardButton("⏱️ التكرار", callback_data="gb:interval")],
        [InlineKeyboardButton("⌛ مدة الحملة", callback_data="gb:duration"), InlineKeyboardButton("👥 المجموعات", callback_data="gb:groups")],
        [InlineKeyboardButton("▶️ تشغيل", callback_data="gb:start"), InlineKeyboardButton("⏸️ إيقاف", callback_data="gb:stop")],
        [InlineKeyboardButton("🚀 إرسال الآن", callback_data="gb:send_now"), InlineKeyboardButton("📊 الحالة", callback_data="gb:status")],
        [InlineKeyboardButton("➕ إضافة مجموعة", callback_data="gb:add_help")],
    ])


def _interval_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for seconds, label in INTERVALS.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"gb:set_interval:{seconds}")])
    rows.append([InlineKeyboardButton("✏️ مدة مخصصة", callback_data="gb:custom_interval")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")])
    return InlineKeyboardMarkup(rows)


def _duration_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"gb:set_duration:{key}")] for key, (_, label) in DURATIONS.items()]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")])
    return InlineKeyboardMarkup(rows)


def _content_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 تغيير المحتوى", callback_data="gb:content_input")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")],
    ])


def register_group_broadcast(app: Any, bot_module: Any, admin_id: int) -> None:
    _ensure_tables(bot_module)
    scheduler_started = False

    async def bootstrap_scheduler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        nonlocal scheduler_started
        if not scheduler_started:
            scheduler_started = True
            context.application.create_task(_scheduler_loop(context.application, bot_module), update=update)

    async def group_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user, chat = update.effective_user, update.effective_chat
        if not user or user.id != admin_id or not chat or chat.type not in ("group", "supergroup"):
            return
        conn = bot_module.get_db()
        try:
            now = _now().isoformat()
            conn.execute(f"""
                INSERT INTO {TABLE_GROUPS} (chat_id,title,enabled,added_at,last_seen)
                VALUES (?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, enabled=1, last_seen=excluded.last_seen
            """, (chat.id, chat.title or "", 1, now, now))
            conn.commit()
        finally:
            conn.close()
        await update.effective_message.reply_text("✅ تمت إضافة/إعادة تفعيل هذه المجموعة للإعلانات الدورية.\n\nيمكنك إدارتها من لوحة إعلان المجموعات.")
        raise ApplicationHandlerStop

    async def group_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user, chat = update.effective_user, update.effective_chat
        if not user or user.id != admin_id or not chat or chat.type not in ("group", "supergroup"):
            return
        conn = bot_module.get_db()
        try:
            conn.execute(f"UPDATE {TABLE_GROUPS} SET enabled=0,last_seen=? WHERE chat_id=?", (_now().isoformat(), chat.id))
            conn.commit()
        finally:
            conn.close()
        await update.effective_message.reply_text("🛑 تم إيقاف الإعلانات لهذه المجموعة.")
        raise ApplicationHandlerStop

    async def open_panel(update, context):
        if not update.effective_user or update.effective_user.id != admin_id:
            return
        text = (
            "📢 <b>إدارة إعلانات المجموعات</b>\n\n"
            "تحكم كامل بالحملة الدورية: المحتوى، التكرار، مدة الحملة، المجموعات، التشغيل والإيقاف، الإرسال الفوري والحالة.\n\n"
            "🔒 هذه الإعدادات منفصلة عن بيانات المستخدمين والتحميلات."
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=_main_keyboard())
        else:
            await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=_main_keyboard())
        context.user_data[STATE] = None
        raise ApplicationHandlerStop

    async def show_groups(query, context):
        conn = bot_module.get_db()
        try:
            rows = conn.execute(f"SELECT chat_id,title,enabled FROM {TABLE_GROUPS} ORDER BY title,chat_id").fetchall()
        finally:
            conn.close()
        buttons = []
        for row in rows[:50]:
            title = (row["title"] or str(row["chat_id"]))[:30]
            icon = "🟢" if row["enabled"] else "⚪"
            action = "disable" if row["enabled"] else "enable"
            buttons.append([InlineKeyboardButton(f"{icon} {title}", callback_data=f"gb:{action}:{row['chat_id']}")])
        buttons.append([InlineKeyboardButton("➕ طريقة إضافة مجموعة", callback_data="gb:add_help")])
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")])
        await query.edit_message_text(
            f"👥 <b>المجموعات المسجلة</b>\n\nالفعالة: {sum(1 for r in rows if r['enabled'])}\nالإجمالي: {len(rows)}\n\n🟢 = تستقبل الإعلان\n⚪ = متوقفة",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
        )

    async def status_text():
        conn = bot_module.get_db()
        try:
            campaign = conn.execute(f"SELECT * FROM {TABLE_CAMPAIGN} WHERE id=1").fetchone()
            groups = conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE_GROUPS} WHERE enabled=1").fetchone()["n"]
            total = conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE_GROUPS}").fetchone()["n"]
        finally:
            conn.close()
        if not campaign:
            return "📊 <b>الحالة</b>\n\n🔴 لا توجد حملة محفوظة.\n👥 المجموعات الفعالة: 0"
        state = "🟢 تعمل" if campaign["enabled"] else "🔴 متوقفة"
        kind = campaign["content_type"] or "غير محدد"
        expires = campaign["expires_at"] or "بدون انتهاء"
        next_send = campaign["next_send_at"] or "—"
        last = campaign["last_sent_at"] or "لم تُرسل بعد"
        return (
            "📊 <b>حالة إعلان المجموعات</b>\n\n"
            f"{state}\n👥 الفعالة: {groups} / {total}\n📦 المحتوى: {kind}\n"
            f"⏱️ التكرار: {_format_interval(int(campaign['interval_seconds']))}\n"
            f"⌛ الانتهاء: {expires}\n🕒 الإرسال القادم: {next_send}\n"
            f"📨 آخر إرسال: {last}\n✅ آخر دورة: {campaign['last_sent_count']} نجاح\n❌ {campaign['last_failed_count']} فشل"
        )

    async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data or ""
        if not update.effective_user or update.effective_user.id != admin_id or not data.startswith("gb:"):
            return
        await query.answer()
        parts = data.split(":", 2)
        action = parts[1]

        if action == "menu":
            await query.edit_message_text("📢 <b>إدارة إعلانات المجموعات</b>\n\nاختر العملية المطلوبة:", parse_mode="HTML", reply_markup=_main_keyboard())
        elif action == "add_help":
            await query.edit_message_text("➕ <b>إضافة مجموعة</b>\n\n1) أضف البوت إلى المجموعة.\n2) اجعل البوت قادرًا على إرسال الرسائل.\n3) من داخل المجموعة أرسل: <code>/group_add</code>\n4) ستظهر المجموعة مباشرة في قائمة المجموعات هنا.\n\nيمكنك لاحقًا إيقافها أو إعادة تفعيلها من لوحة التحكم.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))
        elif action == "groups":
            await show_groups(query, context)
        elif action in ("enable", "disable"):
            chat_id = int(parts[2])
            enabled = 1 if action == "enable" else 0
            conn = bot_module.get_db()
            try:
                conn.execute(f"UPDATE {TABLE_GROUPS} SET enabled=?,last_seen=? WHERE chat_id=?", (enabled, _now().isoformat(), chat_id))
                conn.commit()
            finally:
                conn.close()
            await show_groups(query, context)
        elif action == "interval":
            await query.edit_message_text("⏱️ <b>اختيار التكرار</b>\n\nيمكنك اختيار مدة جاهزة أو إدخال مدة مخصصة مثل 5d أو 12h أو 30m.", parse_mode="HTML", reply_markup=_interval_keyboard())
        elif action == "set_interval":
            seconds = int(parts[2])
            conn = bot_module.get_db()
            try:
                conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET interval_seconds=?,updated_at=? WHERE id=1", (seconds, _now().isoformat()))
                if conn.execute(f"SELECT id FROM {TABLE_CAMPAIGN} WHERE id=1").fetchone() is None:
                    now = _now().isoformat()
                    conn.execute(f"INSERT INTO {TABLE_CAMPAIGN}(id,enabled,interval_seconds,updated_at) VALUES(1,0,?,?)", (seconds, now))
                conn.commit()
            finally:
                conn.close()
            await query.edit_message_text(f"✅ تم ضبط التكرار على: <b>{_format_interval(seconds)}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))
        elif action == "custom_interval":
            context.user_data[STATE] = "custom_interval"
            await query.edit_message_text("✏️ أرسل مدة التكرار الآن، مثل:\n<code>30m</code> أو <code>6h</code> أو <code>5d</code>\nالحد الأدنى 5 دقائق والحد الأقصى سنة.", parse_mode="HTML")
        elif action == "duration":
            await query.edit_message_text("⌛ <b>مدة الحملة</b>\n\nبعد انتهاء المدة ستتوقف الحملة تلقائيًا دون حذفها.", parse_mode="HTML", reply_markup=_duration_keyboard())
        elif action == "set_duration":
            key = parts[2]
            seconds = DURATIONS.get(key, (None, "بدون انتهاء"))[0]
            expires = (_now() + timedelta(seconds=seconds)).isoformat() if seconds else None
            conn = bot_module.get_db()
            try:
                conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET expires_at=?,updated_at=? WHERE id=1", (expires, _now().isoformat()))
                conn.commit()
            finally:
                conn.close()
            await query.edit_message_text(f"✅ تم ضبط مدة الحملة: <b>{DURATIONS.get(key, (None,'بدون انتهاء'))[1]}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))
        elif action in ("content", "content_input"):
            context.user_data[STATE] = "content"
            await query.edit_message_text("📝 <b>محتوى الإعلان</b>\n\nأرسل الآن نصًا أو رابطًا أو صورة أو فيديو أو ملفًا صوتيًا أو رسالة صوتية.\n\nسيتم حفظ Telegram file_id للوسائط، بدون تنزيل ملفات على الخادم.", parse_mode="HTML", reply_markup=_content_keyboard())
        elif action == "start":
            conn = bot_module.get_db()
            try:
                campaign = conn.execute(f"SELECT * FROM {TABLE_CAMPAIGN} WHERE id=1").fetchone()
                groups = conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE_GROUPS} WHERE enabled=1").fetchone()["n"]
                if not campaign or not campaign["content_type"]:
                    await query.edit_message_text("⚠️ لا يمكن تشغيل الحملة قبل تحديد المحتوى.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 تحديد المحتوى", callback_data="gb:content")],[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))
                    return
                if groups == 0:
                    await query.edit_message_text("⚠️ لا توجد مجموعات فعالة. أضف مجموعة أولًا عبر /group_add.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ طريقة الإضافة", callback_data="gb:add_help")],[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))
                    return
                next_send = _now() + timedelta(seconds=int(campaign["interval_seconds"]))
                conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET enabled=1,next_send_at=?,updated_at=? WHERE id=1", (next_send.isoformat(), _now().isoformat()))
                conn.commit()
            finally:
                conn.close()
            await query.edit_message_text(f"🟢 تم تشغيل الحملة.\n\n👥 المجموعات الفعالة: {groups}\n⏱️ التكرار: {_format_interval(int(campaign['interval_seconds']))}\n🕒 أول إرسال مجدول: {next_send.strftime('%Y-%m-%d %H:%M:%S')}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 الحالة", callback_data="gb:status")],[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))
        elif action == "stop":
            conn = bot_module.get_db()
            try:
                conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET enabled=0,updated_at=? WHERE id=1", (_now().isoformat(),))
                conn.commit()
            finally:
                conn.close()
            context.user_data[STATE] = None
            await query.edit_message_text("⏸️ تم إيقاف الحملة. لم يتم حذف المحتوى أو إعداداتها أو بيانات المستخدمين.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ تشغيل لاحقًا", callback_data="gb:start")],[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))
        elif action == "send_now":
            sent, failed = await _send_to_active_groups(query.get_bot(), bot_module)
            conn = bot_module.get_db()
            try:
                conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET last_sent_at=?,last_sent_count=?,last_failed_count=?,updated_at=? WHERE id=1", (_now().isoformat(), sent, failed, _now().isoformat()))
                conn.commit()
            finally:
                conn.close()
            await query.edit_message_text(f"🚀 <b>الإرسال الفوري انتهى</b>\n\n✅ نجح: {sent}\n❌ فشل: {failed}\n\nلم يتم تغيير الجدول الدوري.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 الحالة", callback_data="gb:status")],[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))
        elif action == "status":
            await query.edit_message_text(await status_text(), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data="gb:status")],[InlineKeyboardButton("🔙 رجوع", callback_data="gb:menu")]]))

    async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != admin_id or not update.effective_chat or update.effective_chat.type != "private":
            return
        state = context.user_data.get(STATE)
        if state == "custom_interval":
            seconds = _parse_interval(update.effective_message.text or "")
            if seconds is None:
                await update.effective_message.reply_text("❌ مدة غير صحيحة. مثال صحيح: 30m أو 6h أو 5d.")
                return
            conn = bot_module.get_db()
            try:
                now = _now().isoformat()
                if conn.execute(f"SELECT id FROM {TABLE_CAMPAIGN} WHERE id=1").fetchone():
                    conn.execute(f"UPDATE {TABLE_CAMPAIGN} SET interval_seconds=?,updated_at=? WHERE id=1", (seconds, now))
                else:
                    conn.execute(f"INSERT INTO {TABLE_CAMPAIGN}(id,enabled,interval_seconds,updated_at) VALUES(1,0,?,?)", (seconds, now))
                conn.commit()
            finally:
                conn.close()
            context.user_data[STATE] = None
            await update.effective_message.reply_text(f"✅ تم ضبط التكرار: {_format_interval(seconds)}", reply_markup=_main_keyboard())
            raise ApplicationHandlerStop
        if state == "content":
            content = _extract_content(update.effective_message)
            if not content:
                await update.effective_message.reply_text("❌ أرسل نصًا أو رابطًا أو صورة أو فيديو أو صوتًا/رسالة صوتية.")
                return
            conn = bot_module.get_db()
            try:
                now = _now().isoformat()
                existing = conn.execute(f"SELECT id,enabled,interval_seconds,expires_at FROM {TABLE_CAMPAIGN} WHERE id=1").fetchone()
                if existing:
                    conn.execute(f"""
                        UPDATE {TABLE_CAMPAIGN}
                        SET content_type=?,text=?,file_id=?,caption=?,updated_at=?
                        WHERE id=1
                    """, (content["content_type"], content["text"], content["file_id"], content["caption"], now))
                else:
                    conn.execute(f"""
                        INSERT INTO {TABLE_CAMPAIGN}(id,enabled,interval_seconds,content_type,text,file_id,caption,next_send_at,updated_at)
                        VALUES(1,0,432000,?,?,?,?,?,?)
                    """, (content["content_type"], content["text"], content["file_id"], content["caption"], None, now))
                conn.commit()
            finally:
                conn.close()
            context.user_data[STATE] = None
            await update.effective_message.reply_text("✅ تم حفظ محتوى الإعلان بنجاح.\n\nالحملة لا تبدأ تلقائيًا عند تغيير المحتوى؛ استخدم ▶️ تشغيل عندما تكون جاهزًا.", reply_markup=_main_keyboard())
            raise ApplicationHandlerStop

    async def group_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await open_panel(update, context)

    async def group_broadcast_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await open_panel(update, context)

    app.add_handler(TypeHandler(Update, bootstrap_scheduler), group=-20)
    app.add_handler(CommandHandler("group_add", group_add), group=-10)
    app.add_handler(CommandHandler("group_remove", group_remove), group=-10)
    app.add_handler(CommandHandler("group_broadcast", group_broadcast_command), group=-10)
    app.add_handler(CallbackQueryHandler(group_broadcast_panel, pattern=r"^group_broadcast_panel$"), group=-10)
    app.add_handler(CallbackQueryHandler(callbacks, pattern=r"^gb:"), group=-9)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.ALL, text_input), group=-8)
