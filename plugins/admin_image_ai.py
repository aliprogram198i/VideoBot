"""Isolated owner/admin Image AI Studio for AliBot.

The studio is intentionally independent from the downloader and its databases.
It provides image editing, administrator teaching memory, training-pair capture,
and operation history. It never changes downloader behavior or promotes a model.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .admin_common import authorize

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover
    genai = None
    types = None

logger = logging.getLogger(__name__)

_MODE_KEY = "alibot_image_ai_mode"
_PENDING_TRAINING_KEY = "alibot_image_ai_pending_training"
_MAX_PROMPT = 4000
_MAX_INSTRUCTION = 2000
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MODEL_ENV = "IMAGE_AI_MODEL"
_DEFAULT_MODEL = "gemini-2.5-flash-image"
_PERMISSION = "center.view"


def _root_dir() -> Path:
    configured = os.getenv("IMAGE_AI_DATA_DIR")
    if configured:
        return Path(configured)
    data = Path("/app/data")
    return data / "image_ai" if data.is_dir() else Path(".image_ai")


def _db_path() -> Path:
    return _root_dir() / "image_ai.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    root = _root_dir()
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS image_ai_instructions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instruction TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_image_ai_instructions_active
                ON image_ai_instructions(active, id DESC);
            CREATE TABLE IF NOT EXISTS image_ai_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file_id TEXT NOT NULL,
                result_file_id TEXT,
                instruction TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'dataset',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_image_ai_examples_status
                ON image_ai_examples(status, id DESC);
            CREATE TABLE IF NOT EXISTS image_ai_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                instruction TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                error_type TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_image_ai_jobs_created
                ON image_ai_jobs(id DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _active_instructions() -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT instruction FROM image_ai_instructions "
            "WHERE active=1 ORDER BY id DESC LIMIT 30"
        ).fetchall()
        return [str(row["instruction"]) for row in rows]
    finally:
        conn.close()


def _count(table: str, where: str = "") -> int:
    allowed = {"image_ai_instructions", "image_ai_examples"}
    if table not in allowed:
        raise ValueError("invalid table")
    conn = _connect()
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def _save_instruction(text: str) -> None:
    value = " ".join(text.split()).strip()[:_MAX_INSTRUCTION]
    if not value:
        raise ValueError("empty instruction")
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO image_ai_instructions(instruction, active, created_at, updated_at) "
            "VALUES (?,1,?,?)", (value, now, now)
        )
        conn.commit()
    finally:
        conn.close()


def _save_example(source_file_id: str, result_file_id: str, instruction: str) -> None:
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO image_ai_examples "
            "(source_file_id,result_file_id,instruction,status,created_at,updated_at) "
            "VALUES (?,?,?,'dataset',?,?,?)".replace(",?,?,?)", ",?, ?, ?"),
            (source_file_id, result_file_id, instruction[:_MAX_PROMPT], now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _job_start(instruction: str, model: str) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO image_ai_jobs(mode,instruction,model,status,created_at) "
            "VALUES ('edit',?,?, 'running',?)",
            (instruction[:_MAX_PROMPT], model, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _job_finish(job_id: int, status: str, error_type: str | None = None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE image_ai_jobs SET status=?,error_type=?,completed_at=? WHERE id=?",
            (status, error_type, _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _model() -> str:
    return os.getenv(_MODEL_ENV, _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _client():
    key = os.getenv("IMAGE_AI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key or genai is None:
        return None
    try:
        return genai.Client(api_key=key)
    except Exception as exc:  # pragma: no cover
        logger.warning("Image AI client initialization failed: %s", type(exc).__name__)
        return None


def _build_prompt(instruction: str) -> str:
    learned = _active_instructions()
    rules = "\n".join(f"- {item}" for item in learned) or "- No additional learned instructions."
    return (
        "You are AliBot's private image editing engine.\n"
        "Follow the administrator request precisely. Preserve unrelated source details. "
        "Do not add unrequested elements. Prioritize faithful editing, composition, and legibility.\n\n"
        "Administrator-learned instructions:\n" + rules + "\n\n"
        "Current request:\n" + instruction[:_MAX_PROMPT]
    )


def _extract_image_bytes(response: Any) -> bytes | None:
    for part in getattr(response, "parts", ()) or ():
        inline = getattr(part, "inline_data", None)
        data = getattr(inline, "data", None) if inline else None
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            try:
                return base64.b64decode(data)
            except Exception:
                pass
    return None


async def _run_generation(image_bytes: bytes, mime_type: str, instruction: str) -> tuple[bytes, str]:
    client = _client()
    if client is None or types is None:
        raise RuntimeError("IMAGE_AI_PROVIDER_UNAVAILABLE")
    model = _model()
    prompt = _build_prompt(instruction)

    def generate():
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        return client.models.generate_content(
            model=model,
            contents=[image_part, prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

    response = await asyncio.to_thread(generate)
    output = _extract_image_bytes(response)
    if not output:
        raise RuntimeError("IMAGE_AI_NO_IMAGE_OUTPUT")
    return output, model


def _authorized(update: Update, get_db: Any, owner_id: int) -> bool:
    return authorize(update, get_db, owner_id, _PERMISSION)


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ تعديل صورة", callback_data="admin_image_ai_edit")],
        [InlineKeyboardButton("🎓 تعليم النظام", callback_data="admin_image_ai_teach")],
        [InlineKeyboardButton("📚 بيانات التعلم", callback_data="admin_image_ai_dataset")],
        [InlineKeyboardButton("🧪 اختبار الاتصال", callback_data="admin_image_ai_test")],
        [InlineKeyboardButton("📋 سجل العمليات", callback_data="admin_image_ai_jobs")],
        [InlineKeyboardButton("🔙 مركز التحكم", callback_data="admin_control_center")],
    ])


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 AI Studio", callback_data="admin_image_ai")],
        [InlineKeyboardButton("🔙 مركز التحكم", callback_data="admin_control_center")],
    ])


async def ai_studio_callback(update, context, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _ensure_schema()
    context.user_data.pop(_MODE_KEY, None)
    context.user_data.pop(_PENDING_TRAINING_KEY, None)
    await query.edit_message_text(
        "🎨 <b>AliBot AI Studio</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "🖼️ تعديل الصور بالأوامر\n"
        "🎓 تعليم سلوك النظام وحفظ أمثلة\n"
        f"📚 تعليمات نشطة: <b>{_count('image_ai_instructions','active=1')}</b>\n"
        f"🧩 أمثلة محفوظة: <b>{_count('image_ai_examples','status=\'dataset\'')}</b>\n\n"
        "اختر العملية المطلوبة:", parse_mode="HTML", reply_markup=_keyboard()
    )
    raise ApplicationHandlerStop


async def edit_start_callback(update, context, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    context.user_data[_MODE_KEY] = "edit"
    context.user_data.pop(_PENDING_TRAINING_KEY, None)
    await query.edit_message_text(
        "🖼️ <b>تعديل صورة</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل الصورة مع كتابة أمر التعديل في التعليق.\n"
        "مثال: احذف الخلفية وضع خلفية برتقالية احترافية مع الحفاظ على العنصر الرئيسي.",
        parse_mode="HTML", reply_markup=_back_keyboard()
    )
    raise ApplicationHandlerStop


async def teach_start_callback(update, context, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    context.user_data[_MODE_KEY] = "teach"
    context.user_data.pop(_PENDING_TRAINING_KEY, None)
    await query.edit_message_text(
        "🎓 <b>تعليم النظام</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل صورة مع وصف لما تريد أن يتعلمه النظام منها.\n"
        "ثم أرسل الصورة النهائية/المرجعية لحفظ الزوج كبيانات تدريب.\n\n"
        "أو أرسل رسالة نصية فقط لحفظ تعليمات سلوكية.",
        parse_mode="HTML", reply_markup=_back_keyboard()
    )
    raise ApplicationHandlerStop


async def dataset_callback(update, context, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _ensure_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id,instruction,status,created_at FROM image_ai_examples ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()
    lines = [
        "📚 <b>بيانات تعلم Image AI</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"أمثلة محفوظة: <b>{_count('image_ai_examples','status=\'dataset\'')}</b>",
        f"تعليمات نشطة: <b>{_count('image_ai_instructions','active=1')}</b>", "",
    ]
    if not rows:
        lines.append("لا توجد أمثلة محفوظة بعد.")
    else:
        lines.extend(f"#{r['id']} • {r['status']} • {str(r['instruction'])[:160]}" for r in rows)
    lines.append("\nℹ️ البيانات محفوظة كبيانات تدريب ولا تغيّر أوزان النموذج تلقائياً.")
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=_back_keyboard())
    raise ApplicationHandlerStop


async def jobs_callback(update, context, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _ensure_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id,model,status,error_type,created_at FROM image_ai_jobs ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()
    lines = ["📋 <b>سجل عمليات Image AI</b>", "━━━━━━━━━━━━━━━━━━", ""]
    if not rows:
        lines.append("لا توجد عمليات بعد.")
    else:
        for row in rows:
            err = f" • {row['error_type']}" if row['error_type'] else ""
            lines.append(f"#{row['id']} • {row['status']}{err} • {row['model']}")
            lines.append(f"  {row['created_at']}")
    await query.edit_message_text("\n".join(lines)[:3900], parse_mode="HTML", reply_markup=_back_keyboard())
    raise ApplicationHandlerStop


async def test_callback(update, context, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    model = _model()
    available = _client() is not None
    text = (
        "🧪 <b>اختبار Image AI</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"{'🟢' if available else '🔴'} عميل المزود: {'متاح' if available else 'غير متاح'}\n"
        f"🤖 النموذج: <code>{model}</code>\n\n"
        "سيتم اختبار التوليد الفعلي عند تنفيذ تعديل صورة."
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=_back_keyboard())
    raise ApplicationHandlerStop


async def text_handler(update, context, get_db, owner_id):
    if not _authorized(update, get_db, owner_id):
        return
    mode = context.user_data.get(_MODE_KEY)
    text = (update.effective_message.text or "").strip()
    if mode != "teach" or not text or len(text) > _MAX_INSTRUCTION:
        return
    _ensure_schema()
    _save_instruction(text)
    context.user_data.pop(_MODE_KEY, None)
    await update.effective_message.reply_text(
        "✅ تم حفظ التعليمات في ذاكرة Image AI.\n\n"
        "ستدخل في سياق عمليات التعديل القادمة.", reply_markup=_back_keyboard()
    )
    raise ApplicationHandlerStop


async def photo_handler(update, context, get_db, owner_id):
    if not _authorized(update, get_db, owner_id):
        return
    message = update.effective_message
    mode = context.user_data.get(_MODE_KEY)
    if not message or not message.photo or mode not in {"edit", "teach"}:
        return
    photo = message.photo[-1]
    caption = (message.caption or "").strip()
    if photo.file_size and photo.file_size > _MAX_IMAGE_BYTES:
        await message.reply_text("❌ الصورة أكبر من الحد المسموح به (20MB).")
        raise ApplicationHandlerStop

    if mode == "teach":
        if not caption:
            await message.reply_text("أرسل الصورة مع وصف قصير لما تريد أن يتعلمه النظام منها.")
            raise ApplicationHandlerStop
        context.user_data[_PENDING_TRAINING_KEY] = {
            "source_file_id": photo.file_id,
            "instruction": caption[:_MAX_PROMPT],
        }
        context.user_data.pop(_MODE_KEY, None)
        await message.reply_text(
            "📚 تم تسجيل صورة المثال.\n\nأرسل الآن الصورة النهائية/المرجعية لحفظ الزوج كبيانات تدريب.",
            reply_markup=_back_keyboard()
        )
        raise ApplicationHandlerStop

    if not caption:
        await message.reply_text("❌ اكتب أمر التعديل في تعليق الصورة.")
        raise ApplicationHandlerStop

    model = _model()
    job_id = _job_start(caption, model)
    status_message = await message.reply_text("🖼️ جاري تنفيذ التعديل... ⏳")
    temp_path = None
    try:
        telegram_file = await photo.get_file()
        with tempfile.NamedTemporaryFile(prefix="alibot-ai-", suffix=".jpg", delete=False) as handle:
            temp_path = handle.name
        await telegram_file.download_to_drive(temp_path)
        image_bytes = Path(temp_path).read_bytes()
        output, used_model = await _run_generation(image_bytes, "image/jpeg", caption)
        if len(output) > 20 * 1024 * 1024:
            raise RuntimeError("IMAGE_AI_OUTPUT_TOO_LARGE")
        _job_finish(job_id, "success")
        await status_message.delete()
        await message.reply_photo(
            photo=output,
            caption=f"🎨 AliBot Image AI\n🤖 {used_model}",
            reply_markup=_back_keyboard(),
        )
    except Exception as exc:
        _job_finish(job_id, "failed", type(exc).__name__)
        logger.exception("Image AI edit failed: %s", type(exc).__name__)
        await status_message.edit_text(
            "❌ تعذر تنفيذ تعديل الصورة.\n\n"
            f"السبب التقني: <code>{type(exc).__name__}</code>\n\n"
            "لم يتم تعديل أي جزء من نظام التنزيل.",
            parse_mode="HTML", reply_markup=_back_keyboard()
        )
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass
    raise ApplicationHandlerStop


async def training_result_photo_handler(update, context, get_db, owner_id):
    if not _authorized(update, get_db, owner_id):
        return
    message = update.effective_message
    pending = context.user_data.get(_PENDING_TRAINING_KEY)
    if not message or not pending or not message.photo:
        return
    result_photo = message.photo[-1]
    _ensure_schema()
    _save_example(pending["source_file_id"], result_photo.file_id, pending["instruction"])
    context.user_data.pop(_PENDING_TRAINING_KEY, None)
    await message.reply_text(
        "✅ تم حفظ زوج الصورة الأصلية/النتيجة كبيانات تدريب.\n\n"
        "لن يتم تغيير أوزان النموذج أو نسخة الإنتاج تلقائياً.", reply_markup=_back_keyboard()
    )
    raise ApplicationHandlerStop


def register_admin_image_ai(app: Any, get_db: Any, owner_id: int) -> None:
    """Register Image AI Studio once with isolated callback/message ownership."""
    _ensure_schema()
    callbacks = [
        (r"^admin_image_ai$", ai_studio_callback),
        (r"^admin_image_ai_edit$", edit_start_callback),
        (r"^admin_image_ai_teach$", teach_start_callback),
        (r"^admin_image_ai_dataset$", dataset_callback),
        (r"^admin_image_ai_jobs$", jobs_callback),
        (r"^admin_image_ai_test$", test_callback),
    ]
    for pattern, callback in callbacks:
        app.add_handler(
            CallbackQueryHandler(lambda u, c, cb=callback: cb(u, c, get_db, owner_id), pattern=pattern),
            group=-100,
        )
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE,
            lambda u, c: training_result_photo_handler(u, c, get_db, owner_id)
            if c.user_data.get(_PENDING_TRAINING_KEY)
            else photo_handler(u, c, get_db, owner_id),
        ),
        group=-150,
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            lambda u, c: text_handler(u, c, get_db, owner_id),
        ),
        group=-150,
    )
