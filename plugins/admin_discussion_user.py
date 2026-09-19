"""Telegram MTProto user-account discussion manager for AliBot.

This module is intentionally separate from the Bot API.  It uses one dedicated
Telegram *user* account, authenticated with MTProto, to join administrator-
selected public/private discussion groups and send explicitly requested test
messages.  It never auto-discovers or auto-publishes to chats.
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .admin_common import authorize
from .admin_control_center import audit

try:
    from telethon import TelegramClient, errors
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest
except ImportError:  # pragma: no cover - dependency is installed in production
    TelegramClient = None
    errors = None
    JoinChannelRequest = None
    ImportChatInviteRequest = None


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mtproto_discussions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    chat_id INTEGER UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    username TEXT,
    chat_type TEXT NOT NULL DEFAULT 'unknown',
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    joined_at TEXT,
    last_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

PHONE_RE = re.compile(r"^\\+?[0-9][0-9 ()-]{6,24}$")
PUBLIC_RE = re.compile(r"^https?://t\\.me/[A-Za-z0-9_]{4,}$", re.I)
PRIVATE_RE = re.compile(r"^https?://t\\.me/(?:\\+|joinchat/)[A-Za-z0-9_-]+$", re.I)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_mtproto_discussions(get_db) -> None:
    conn = get_db()
    conn.execute(TABLE_SQL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mtproto_discussions_enabled "
        "ON mtproto_discussions(enabled, updated_at DESC)"
    )
    conn.commit()
    conn.close()


def _normalize_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("الرابط فارغ.")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.netloc.lower() not in {"t.me", "www.t.me"}:
        raise ValueError("يجب أن يكون الرابط من Telegram بصيغة t.me.")
    clean = f"https://t.me/{parsed.path.lstrip('/')}"
    if parsed.query or parsed.fragment:
        raise ValueError("الرابط يجب أن يكون رابط محادثة مباشرًا بدون query أو fragment.")
    if not (PUBLIC_RE.fullmatch(clean) or PRIVATE_RE.fullmatch(clean)):
        raise ValueError("الرابط غير مدعوم. استخدم t.me/username أو t.me/+invite أو t.me/joinchat/invite.")
    return clean


def _invite_hash(url: str) -> str | None:
    path = urlparse(url).path.strip("/")
    if path.startswith("+"):
        return path[1:]
    if path.lower().startswith("joinchat/"):
        return path.split("/", 1)[1]
    return None


def _safe_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:240] or type(exc).__name__


class DiscussionUserManager:
    def __init__(self, get_db):
        self.get_db = get_db
        self.api_id = self._read_api_id()
        self.api_hash = (os.getenv("ALIBOT_MTPROTO_API_HASH") or "").strip()
        self.session_path = Path(
            os.getenv("ALIBOT_MTPROTO_SESSION_PATH", "/app/data/mtproto/ali_user.session")
        )
        self._client = None
        self._phone = None
        self._last_test_at = None

    @staticmethod
    def _read_api_id() -> int | None:
        raw = (os.getenv("ALIBOT_MTPROTO_API_ID") or "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    @property
    def configured(self) -> bool:
        return TelegramClient is not None and bool(self.api_id and self.api_hash)

    def _client_instance(self):
        if not self.configured:
            return None
        if self._client is None:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            self._client = TelegramClient(
                str(self.session_path),
                self.api_id,
                self.api_hash,
                flood_sleep_threshold=60,
            )
        if self.session_path.exists():
            try:
                os.chmod(self.session_path, 0o600)
            except OSError:
                pass
        return self._client

    async def connect(self):
        client = self._client_instance()
        if client is None:
            return None
        if not client.is_connected():
            await client.connect()
        return client

    async def authorized(self) -> bool:
        client = await self.connect()
        if client is None:
            return False
        try:
            return bool(await client.is_user_authorized())
        except Exception:
            return False

    async def me(self):
        client = await self.connect()
        if client is None or not await client.is_user_authorized():
            return None
        return await client.get_me()

    async def request_code(self, phone: str) -> None:
        phone = phone.strip()
        if not PHONE_RE.fullmatch(phone):
            raise ValueError("أرسل رقم الهاتف بصيغة دولية مثل +15551234567.")
        client = await self.connect()
        if client is None:
            raise RuntimeError("MTPROTO غير مهيأ: يلزم API ID وAPI HASH.")
        if await client.is_user_authorized():
            raise RuntimeError("الحساب مرتبط بالفعل.")
        await client.send_code_request(phone)
        self._phone = phone

    async def submit_code(self, code: str) -> str:
        if not self._phone:
            raise RuntimeError("انتهت جلسة تسجيل الدخول. ابدأ الربط من جديد.")
        client = await self.connect()
        try:
            user = await client.sign_in(self._phone, code.strip().replace(" ", ""))
        except errors.SessionPasswordNeededError:
            return "password"
        await self._verify_user_account(user)
        return "ok"

    async def submit_password(self, password: str) -> None:
        if not self._phone:
            raise RuntimeError("انتهت جلسة تسجيل الدخول. ابدأ الربط من جديد.")
        client = await self.connect()
        user = await client.sign_in(password=password)
        await self._verify_user_account(user)

    async def _verify_user_account(self, user) -> None:
        if getattr(user, "bot", False):
            await self.disconnect()
            raise RuntimeError("هذا الحساب Bot وليس حساب مستخدم. يجب ربط حساب Telegram عادي.")
        self._phone = None

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()

    async def logout(self) -> None:
        client = await self.connect()
        if client is None:
            return
        if await client.is_user_authorized():
            await client.log_out()
        self._client = None
        self._phone = None

    async def join_discussion(self, raw_url: str) -> dict:
        url = _normalize_url(raw_url)
        client = await self.connect()
        if client is None or not await client.is_user_authorized():
            raise RuntimeError("اربط حساب المستخدم أولًا من قسم حساب المستخدم.")

        invite_hash = _invite_hash(url)
        if invite_hash:
            try:
                updates = await client(ImportChatInviteRequest(invite_hash))
                chats = getattr(updates, "chats", None) or []
                entity = chats[0] if chats else await client.get_entity(url)
            except Exception as exc:
                if errors and isinstance(exc, errors.UserAlreadyParticipantError):
                    entity = await client.get_entity(url)
                else:
                    raise
        else:
            entity = await client.get_entity(url)
            if getattr(entity, "broadcast", False) and not getattr(entity, "megagroup", False):
                raise ValueError("الرابط يشير إلى قناة نشر، وليس مجموعة مناقشة قابلة للنشر من حساب المستخدم.")
            try:
                if getattr(entity, "megagroup", False):
                    await client(JoinChannelRequest(entity))
                else:
                    raise ValueError("هذا الرابط ليس Supergroup/Discussion صالحًا.")
            except Exception as exc:
                if errors and isinstance(exc, errors.UserAlreadyParticipantError):
                    pass
                elif errors and isinstance(exc, errors.InviteRequestSentError):
                    self._upsert(url, entity, "join_requested", None)
                    return {"status": "join_requested", "entity": entity}
                else:
                    raise

        if getattr(entity, "broadcast", False) and not getattr(entity, "megagroup", False):
            raise ValueError("الرابط يشير إلى قناة نشر، وليس مجموعة مناقشة قابلة للنشر.")
        self._upsert(url, entity, "joined", None)
        return {"status": "joined", "entity": entity}

    async def verify_discussion(self, row_id: int) -> dict:
        row = self._get(row_id)
        if not row:
            raise ValueError("المناقشة غير موجودة.")
        client = await self.connect()
        if client is None or not await client.is_user_authorized():
            raise RuntimeError("حساب المستخدم غير مرتبط.")
        entity = await client.get_entity(row["chat_id"] or row["url"])
        permissions = await client.get_permissions(entity)
        can_send = not getattr(permissions, "is_banned", False) and not getattr(permissions, "has_left", False)
        self._mark_verified(row_id, "joined" if can_send else "write_forbidden", None)
        return {"entity": entity, "can_send": can_send}

    async def send_test(self, row_id: int) -> int:
        row = self._get(row_id)
        if not row:
            raise ValueError("المناقشة غير موجودة.")
        client = await self.connect()
        if client is None or not await client.is_user_authorized():
            raise RuntimeError("حساب المستخدم غير مرتبط.")
        entity = await client.get_entity(row["chat_id"] or row["url"])
        permissions = await client.get_permissions(entity)
        if getattr(permissions, "is_banned", False) or getattr(permissions, "has_left", False):
            raise RuntimeError("الحساب ممنوع من الكتابة في هذه المناقشة.")
        now = datetime.now(timezone.utc)
        if self._last_test_at is not None:
            elapsed = (now - self._last_test_at).total_seconds()
            if elapsed < 30:
                raise RuntimeError(f"انتظر {30 - int(elapsed)} ثانية قبل اختبار نشر آخر.")
        sent = await client.send_message(
            entity,
            "🧪 AliBot — اختبار نشر من حساب المستخدم المرتبط.\n\n"
            "إذا ظهرت هذه الرسالة هنا، فمسار MTProto يعمل بشكل صحيح.",
        )
        self._last_test_at = now
        self._mark_verified(row_id, "ready", None)
        return int(sent.id)

    def _get(self, row_id: int):
        conn = self.get_db()
        row = conn.execute(
            "SELECT * FROM mtproto_discussions WHERE id=? AND enabled=1", (row_id,)
        ).fetchone()
        conn.close()
        return row

    def _upsert(self, url: str, entity, status: str, error_text: str | None):
        now = _now()
        chat_id = int(getattr(entity, "id", 0) or 0) or None
        title = str(getattr(entity, "title", "") or "")
        username = getattr(entity, "username", None)
        chat_type = "supergroup" if getattr(entity, "megagroup", False) else "group"
        conn = self.get_db()
        conn.execute(
            """
            INSERT INTO mtproto_discussions
                (url, chat_id, title, username, chat_type, enabled, status,
                 last_error, joined_at, last_verified_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                chat_id=excluded.chat_id,
                title=excluded.title,
                username=excluded.username,
                chat_type=excluded.chat_type,
                enabled=1,
                status=excluded.status,
                last_error=excluded.last_error,
                joined_at=COALESCE(mtproto_discussions.joined_at, excluded.joined_at),
                last_verified_at=excluded.last_verified_at,
                updated_at=excluded.updated_at
            """,
            (
                url, chat_id, title, username, chat_type, status, error_text,
                now if status == "joined" else None,
                now if status == "ready" else None,
                now, now,
            ),
        )
        conn.commit()
        conn.close()

    def _mark_verified(self, row_id: int, status: str, error_text: str | None):
        conn = self.get_db()
        conn.execute(
            "UPDATE mtproto_discussions SET status=?, last_error=?, "
            "last_verified_at=?, updated_at=? WHERE id=?",
            (status, error_text, _now(), _now(), row_id),
        )
        conn.commit()
        conn.close()

    def list_rows(self):
        conn = self.get_db()
        rows = conn.execute(
            "SELECT * FROM mtproto_discussions WHERE enabled=1 ORDER BY id DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return rows


def _menu(manager: DiscussionUserManager, rows=None) -> InlineKeyboardMarkup:
    rows = rows if rows is not None else manager.list_rows()
    buttons = [
        [InlineKeyboardButton("🔐 حساب المستخدم", callback_data="admin_mtproto_account"),
         InlineKeyboardButton("➕ إضافة مناقشة", callback_data="admin_mtproto_add")],
    ]
    for row in rows[:15]:
        title = str(row["title"] or row["username"] or row["url"])[:24]
        buttons.append([
            InlineKeyboardButton(f"🧪 اختبار: {title}", callback_data=f"admin_mtproto_test_{row['id']}"),
            InlineKeyboardButton("🔄 تحقق", callback_data=f"admin_mtproto_verify_{row['id']}"),
        ])
    buttons += [
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_mtproto_menu")],
        [InlineKeyboardButton("🎛️ لوحة القيادة", callback_data="admin_home")],
    ]
    return InlineKeyboardMarkup(buttons)


async def _render_menu(update, manager, get_db, owner_id, *, edit=True):
    text = [
        "📢 <b>مناقشات Telegram — حساب مستخدم</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "هذا القسم يستخدم حساب Telegram عادي عبر MTProto.",
        "الرابط الذي تضيفه هنا هو الذي سيُستخدم للانضمام؛ لا يوجد اكتشاف تلقائي للمجموعات.",
        "",
    ]
    if not manager.configured:
        text += ["🔴 <b>MTProto غير مهيأ</b>", "أضف ALIBOT_MTPROTO_API_ID و ALIBOT_MTPROTO_API_HASH في Railway staging."]
    else:
        me = await manager.me()
        if me:
            identity = f"@{me.username}" if me.username else (me.first_name or str(me.id))
            text += [f"🟢 <b>الحساب مرتبط:</b> {html.escape(identity)}", f"🆔 <code>{me.id}</code>"]
        else:
            text += ["🟠 <b>الحساب غير مرتبط</b>", "استخدم 🔐 حساب المستخدم لبدء الربط."]
    rows = manager.list_rows()
    text += ["", f"📚 المناقشات المحفوظة: <b>{len(rows)}</b>"]
    if rows:
        for row in rows[:15]:
            state = {"joined": "🟢 Joined", "ready": "🟢 Ready", "join_requested": "🟡 طلب انضمام", "write_forbidden": "🔴 ممنوع كتابة"}.get(row["status"], "🟠 Pending")
            text.append(f"• {html.escape(str(row['title'] or row['url']))} — {state}")
    else:
        text.append("لا توجد مناقشات مضافة بعد.")
    markup = _menu(manager, rows)
    if edit:
        await update.callback_query.edit_message_text("\n".join(text), parse_mode="HTML", reply_markup=markup)
    else:
        await update.effective_message.reply_text("\n".join(text), parse_mode="HTML", reply_markup=markup)


async def _account_callback(update, context, manager, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    me = await manager.me()
    if me:
        identity = f"@{me.username}" if me.username else (me.first_name or str(me.id))
        text = (
            "🔐 <b>حساب المستخدم المرتبط</b>\n\n"
            f"🟢 {html.escape(identity)}\n"
            f"🆔 <code>{me.id}</code>\n\n"
            "هذا حساب مستخدم حقيقي، وليس AliBot Bot API."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚪 فصل الحساب", callback_data="admin_mtproto_logout")],
            [InlineKeyboardButton("🔙 مناقشات Telegram", callback_data="admin_mtproto_menu")],
        ])
    else:
        text = (
            "🔐 <b>ربط حساب Telegram عادي</b>\n\n"
            "سيتم استخدام هذا الحساب فقط للانضمام إلى الروابط التي تضيفها أنت.\n"
            "لا ترسل بيانات الدخول إلى أي مكان آخر."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 بدء الربط", callback_data="admin_mtproto_login")],
            [InlineKeyboardButton("🔙 مناقشات Telegram", callback_data="admin_mtproto_menu")],
        ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def _start_login(update, context, manager, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    if not manager.configured:
        await query.edit_message_text(
            "❌ <b>MTProto غير مهيأ</b>\n\n"
            "أضف في Railway staging:\n"
            "• <code>ALIBOT_MTPROTO_API_ID</code>\n"
            "• <code>ALIBOT_MTPROTO_API_HASH</code>\n\n"
            "ثم أعد فتح القسم.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_mtproto_menu")]]),
        )
        return
    context.user_data["mtproto_state"] = "phone"
    await query.edit_message_text(
        "📱 <b>الخطوة 1/3</b>\n\nأرسل رقم حساب Telegram العادي بصيغة دولية، مثل:\n<code>+15551234567</code>",
        parse_mode="HTML",
    )


async def _input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, manager, get_db, owner_id):
    if not update.effective_user or update.effective_user.id != owner_id:
        return
    state = context.user_data.get("mtproto_state")
    if not state:
        return
    message = update.effective_message
    if message is None or not message.text:
        return
    value = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass
    chat = update.effective_chat
    try:
        if state == "phone":
            await manager.request_code(value)
            context.user_data["mtproto_state"] = "code"
            await chat.send_message("📨 أرسلت Telegram رمز تسجيل الدخول. أرسله هنا الآن. سيتم حذف رسالتك فورًا.")
        elif state == "code":
            result = await manager.submit_code(value)
            if result == "password":
                context.user_data["mtproto_state"] = "password"
                await chat.send_message("🔑 الحساب محمي بخطوتين. أرسل كلمة مرور 2FA هنا.")
            else:
                context.user_data.pop("mtproto_state", None)
                await chat.send_message("✅ تم ربط حساب المستخدم بنجاح.", reply_markup=_menu(manager))
        elif state == "password":
            await manager.submit_password(value)
            context.user_data.pop("mtproto_state", None)
            await chat.send_message("✅ تم ربط حساب المستخدم بنجاح.", reply_markup=_menu(manager))
        elif state == "discussion_url":
            result = await manager.join_discussion(value)
            context.user_data.pop("mtproto_state", None)
            entity = result["entity"]
            title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(getattr(entity, "id", ""))
            status = result["status"]
            text = "✅ تم الانضمام إلى المناقشة." if status == "joined" else "🟡 تم إرسال طلب الانضمام، وينتظر موافقة مشرف المجموعة."
            await chat.send_message(f"{text}\n\n💬 <b>{html.escape(str(title))}</b>", parse_mode="HTML", reply_markup=_menu(manager))
    except Exception as exc:
        if state in {"phone", "code", "password"}:
            context.user_data.pop("mtproto_state", None)
        audit(get_db, owner_id, "mtproto_operation_failed", None, type(exc).__name__)
        await chat.send_message(f"❌ {html.escape(_safe_error(exc))}")
    raise ApplicationHandlerStop


async def _add_callback(update, context, manager, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    if not await manager.authorized():
        await query.edit_message_text(
            "❌ اربط حساب المستخدم أولًا.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔐 حساب المستخدم", callback_data="admin_mtproto_account")]]),
        )
        return
    context.user_data["mtproto_state"] = "discussion_url"
    await query.edit_message_text(
        "🔗 <b>إضافة مناقشة</b>\n\n"
        "أرسل رابط المناقشة، مثل:\n"
        "<code>https://t.me/ExampleDiscussion</code>\n\n"
        "وللمناقشات الخاصة استخدم رابط الدعوة.",
        parse_mode="HTML",
    )


async def _test_callback(update, context, manager, get_db, owner_id):
    query = update.callback_query
    await query.answer("جارِ إرسال اختبار...", show_alert=False)
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    try:
        row_id = int((query.data or "").rsplit("_", 1)[1])
        message_id = await manager.send_test(row_id)
        audit(get_db, owner_id, "mtproto_discussion_test", row_id, f"message_id={message_id}")
        await query.edit_message_text(
            f"✅ <b>نجح اختبار النشر</b>\n\n📨 Message ID: <code>{message_id}</code>\n"
            "تم الإرسال من حساب المستخدم المرتبط، وليس من Bot API.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 مناقشات Telegram", callback_data="admin_mtproto_menu")]]),
        )
    except Exception as exc:
        audit(get_db, owner_id, "mtproto_discussion_test_failed", None, type(exc).__name__)
        await query.edit_message_text(
            f"❌ <b>فشل الاختبار</b>\n\n{html.escape(_safe_error(exc))}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 مناقشات Telegram", callback_data="admin_mtproto_menu")]]),
        )


async def _verify_callback(update, context, manager, get_db, owner_id):
    query = update.callback_query
    await query.answer("جارِ التحقق...")
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    try:
        row_id = int((query.data or "").rsplit("_", 1)[1])
        result = await manager.verify_discussion(row_id)
        state = "🟢 الحساب يستطيع الكتابة." if result["can_send"] else "🔴 الحساب لا يستطيع الكتابة."
        await query.edit_message_text(
            f"🔄 <b>نتيجة التحقق</b>\n\n{state}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 مناقشات Telegram", callback_data="admin_mtproto_menu")]]),
        )
    except Exception as exc:
        await query.edit_message_text(
            f"❌ <b>فشل التحقق</b>\n\n{html.escape(_safe_error(exc))}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 مناقشات Telegram", callback_data="admin_mtproto_menu")]]),
        )


async def _logout_callback(update, context, manager, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    try:
        await manager.logout()
        audit(get_db, owner_id, "mtproto_logout")
        await query.edit_message_text("🚪 تم فصل حساب المستخدم وإلغاء جلسة MTProto.", reply_markup=_menu(manager))
    except Exception as exc:
        await query.edit_message_text(f"❌ {html.escape(_safe_error(exc))}", reply_markup=_menu(manager))


async def _menu_callback(update, context, manager, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not authorize(update, get_db, owner_id, "center.view"):
        return
    await _render_menu(update, manager, get_db, owner_id)


async def _command(update, context, manager, get_db, owner_id):
    if not update.effective_user or update.effective_user.id != owner_id:
        return
    await update.effective_message.reply_text(
        "📢 <b>مناقشات Telegram — حساب مستخدم</b>\n\n"
        "استخدم الأزرار لإدارة حساب المستخدم والمناقشات المحددة.",
        parse_mode="HTML",
        reply_markup=_menu(manager),
    )
    raise ApplicationHandlerStop


def register_discussion_user_manager(app, get_db, owner_id):
    init_mtproto_discussions(get_db)
    manager = DiscussionUserManager(get_db)
    app.bot_data["discussion_user_manager"] = manager

    app.add_handler(CommandHandler("admindiscussions", lambda u, c: _command(u, c, manager, get_db, owner_id)), group=-200)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            lambda u, c: _input_handler(u, c, manager, get_db, owner_id),
        ),
        group=-250,
    )
    patterns = {
        r"^admin_mtproto_menu$": _menu_callback,
        r"^admin_mtproto_account$": _account_callback,
        r"^admin_mtproto_login$": _start_login,
        r"^admin_mtproto_add$": _add_callback,
        r"^admin_mtproto_test_\\d+$": _test_callback,
        r"^admin_mtproto_verify_\\d+$": _verify_callback,
        r"^admin_mtproto_logout$": _logout_callback,
    }
    for pattern, handler in patterns.items():
        app.add_handler(
            CallbackQueryHandler(
                lambda u, c, handler=handler: handler(u, c, manager, get_db, owner_id),
                pattern=pattern,
            ),
            group=-200,
        )
