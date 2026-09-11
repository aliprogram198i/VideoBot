"""Isolated owner-only Image AI Studio for AliBot.

The studio is deliberately separate from the downloader and the production
smart-learning runtime. It provides:
- image editing through the configured Gemini image model;
- persistent admin teaching instructions/examples in a separate SQLite DB;
- explicit dataset collection without silently training or changing production;
- deterministic, bounded local storage and cleanup of temporary media.

No downloader tables are modified and no model is promoted automatically.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from .admin_common import authorize

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover - optional provider dependency
    genai = None
    types = None

logger = logging.getLogger(__name__)

_OWNER_PERMISSION = "center.view"
_MODE_KEY = "alibot_image_ai_mode"
_PENDING_EDIT_KEY = "alibot_image_ai_pending_edit"
_PENDING_TRAINING_KEY = "alibot_image_ai_pending_training"
_MAX_PROMPT = 4000
_MAX_INSTRUCTION = 2000
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_EXAMPLES_IN_PROMPT = 8
_MODEL_ENV = "IMAGE_AI_MODEL"
_DEFAULT_MODEL = "gemini-2.5-flash-image"


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
    conn.execute("PRAGMA journal_mode=WAL")
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


def _instruction_count() -> int:
    conn = _connect()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM image_ai_instructions WHERE active=1"
        ).fetchone()[0])
    finally:
        conn.close()


def _example_count() -> int:
    conn = _connect()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM image_ai_examples WHERE status='dataset'"
        ).fetchone()[0])
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
            "VALUES (?, 1, ?, ?)",
            (value, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _save_example(source_file_id: str, result_file_id: str | None, instruction: str) -> None:
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO image_ai_examples "
            "(source_file_id, result_file_id, instruction, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'dataset', ?, ?)",
            (source_file_id, result_file_id, instruction[:_MAX_PROMPT], now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _job_start(mode: str, instruction: str, model: str) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO image_ai_jobs(mode, instruction, model, status, created_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (mode, instruction[:_MAX_PROMPT], model, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _job_finish(job_id: int, status: str, error_type: str | None = None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE image_ai_jobs SET status=?, error_type=?, completed_at=? WHERE id=?",
            (status, error_type, _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _client():
    key = os.getenv("GEMINI_API_KEY")
    if not key or genai is None:
        return None
    try:
        return genai.Client(api_key=key)
    except Exception as exc:  # pragma: no cover
        logger.warning("Image AI client initialization failed: %s", type(exc).__name__)
        return None


def _build_prompt(instruction: str) -> str:
    instructions = _active_instructions()
    learned = "\n".join(f"- {item}" for item in instructions[:30])
    if not learned:
        learned = "- لا توجد تعليمات تعليمية محفوظة بعد."
    return (
        "You are AliBot's private image editing engine.\n"
        "Apply the administrator's requested edit precisely. Preserve unrelated "
        "parts of the source image whenever the request does not ask to change them.\n"
        "Do not invent extra design elements. Prioritize composition, legibility, "
        "and faithful execution of the requested instruction.\n\n"
        "Administrator-learned instructions:\n"
        f"{learned}\n\n"
        "Current edit request:\n"
        f"{instruction[:_MAX_PROMPT]}"
    )


def _extract_image_bytes(response: Any) -> bytes | None:
    for part in getattr(response, "parts", ()) or ():
        inline = getattr(part, "inline_data", None)
        if inline is None:
            continue
        data = getattr(inline, "data", None)
        if data is None:
            continue
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            try:
                return base64.b64decode(data)
            except Exception:
                continue
    return None


def _generate_image(image_bytes: bytes, mime_type: str, instruction: str) -> tuple[bytes, str]:
    client = _client()
    if client is None or types is None:
        raise RuntimeError("IMAGE_AI_PROVIDER_UNAVAILABLE")
    model = os.getenv(_MODEL_ENV, _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    prompt = _build_prompt(instruction)

    def generate():
        part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        return client.models.generate_content(
            model=model,
            contents=[part, prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

    response = asyncio.run(asyncio.to_thread(generate))
    output = _extract_image_bytes(response)
    if not output:
        raise RuntimeError("IMAGE_AI_NO_IMAGE_OUTPUT")
    return output, model


async def _run_generation(image_bytes: bytes, mime_type: str, instruction: str) -> tuple[bytes, str]:
    client = _client()
    if client is None or types is None:
        raise RuntimeError("IMAGE_AI_PROVIDER_UNAVAILABLE")
    model = os.getenv(_MODEL_ENV, _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    prompt = _build_prompt(instruction)

    def generate():
        part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        return client.models.generate_content(
            model=model,
            contents=[part, prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

    response = await asyncio.to_thread(generate)
    output = _extract_image_bytes(response)
    if not output:
        raise RuntimeError("IMAGE_AI_NO_IMAGE_OUTPUT")
    return output, model


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


def _authorized(update: Update, get_db: Any, owner_id: int) -> bool:
    return authorize(update, get_db, owner_id, _OWNER_PERMISSION)


async def ai_studio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _ensure_schema()
    context.user_data.pop(_MODE_KEY, None)
    context.user_data.pop(_PENDING_EDIT_KEY, None)
    context.user_data.pop(_PENDING_TRAINING_KEY, None)
    await query.edit_message_text(
        "🎨 <b>AliBot AI Studio</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🖼️ تعديل الصور بالأوامر\n"
        "🎓 تعليم سلوك النظام وحفظ أمثلة\n"
        f"📚 تعليمات نشطة: <b>{_instruction_count()}</b>\n"
        f"🧩 أمثلة محفوظة: <b>{_example_count()}</b>\n\n"
        "اختر العملية المطلوبة:",
        parse_mode="HTML",
        reply_markup=_keyboard(),
    )


async def edit_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    context.user_data[_MODE_KEY] = "edit"
    context.user_data.pop(_PENDING_EDIT_KEY, None)
    await query.edit_message_text(
        "🖼️ <b>تعديل صورة</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل الصورة مع كتابة الأمر في التعليق.\n"
        "مثال: <i>احذف الخلفية وضع خلفية برتقالية احترافية مع الحفاظ على العنصر الرئيسي.</i>",
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
    )


async def teach_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    context.user_data[_MODE_KEY] = "teach"
    context.user_data.pop(_PENDING_TRAINING_KEY, None)
    await query.edit_message_text(
        "🎓 <b>تعليم النظام</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل أولاً صورة المثال مع وصف ما تريد أن يتعلمه منها.\n"
        "بعدها سيرسل لك النظام طلب إرسال الصورة النهائية/المرجعية لحفظ الزوج كبيانات تدريب.\n\n"
        "يمكنك أيضاً إرسال رسالة نصية فقط لحفظ تعليمات سلوكية بدون صورة.",
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
    )


async def dataset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _ensure_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, instruction, status, created_at FROM image_ai_examples "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()
    text = [
        "📚 <b>بيانات تعلم Image AI</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"أمثلة محفوظة: <b>{_example_count()}</b>",
        f"تعليمات نشطة: <b>{_instruction_count()}</b>",
        "",
    ]
    if not rows:
        text.append("لا توجد أمثلة محفوظة بعد.")
    else:
        for row in rows:
            text.append(f"#{row['id']} • {row['status']} • {str(row['instruction'])[:160]}")
    text.extend(["", "ℹ️ الحفظ هنا يجمع بيانات التدريب ولا يغير أوزان النموذج تلقائياً."])
    await query.edit_message_text("\n".join(text), parse_mode="HTML", reply_markup=_back_keyboard())


async def jobs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    _ensure_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, mode, model, status, error_type, created_at "
            "FROM image_ai_jobs ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()
    lines = ["📋 <b>سجل عمليات Image AI</b>", "━━━━━━━━━━━━━━━━━━", ""]
    if not rows:
        lines.append("لا توجد عمليات بعد.")
    else:
        for row in rows:
            err = f" • {row['error_type']}" if row['error_type'] else ""
            lines.append(f"#{row['id']} • {row['mode']} • {row['status']}{err}")
            lines.append(f"  {row['model']} • {row['created_at']}")
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=_back_keyboard())


async def test_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    client = _client()
    model = os.getenv(_MODEL_ENV, _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    if client is None:
        await query.edit_message_text(
            "🧪 <b>اختبار Image AI</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "❌ مزود الصور غير متاح. تأكد من GEMINI_API_KEY ومن تثبيت google-genai.",
            parse_mode="HTML", reply_markup=_back_keyboard(),
        )
        return
    await query.edit_message_text(
        f"🧪 <b>اختبار Image AI</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🟡 العميل متاح\n🤖 النموذج: <code>{model}</code>\n\n"
        "سيتم اختبار التوليد الفعلي عند تنفيذ تعديل صورة.",
        parse_mode="HTML", reply_markup=_back_keyboard(),
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    if not _authorized(update, get_db, owner_id):
        return
    text = (update.effective_message.text or "").strip()
    mode = context.user_data.get(_MODE_KEY)
    if not mode or not text or len(text) > _MAX_INSTRUCTION:
        return
    if mode == "teach":
        _ensure_schema()
        _save_instruction(text)
        await update.effective_message.reply_text(
            "✅ تم حفظ التعليمات في ذاكرة Image AI.\n\n"
            "هذه التعليمات ستدخل في سياق عمليات التعديل القادمة.\n"
            "يمكنك الآن إرسال مثال بصورة، أو العودة إلى AI Studio.",
            reply_markup=_back_keyboard(),
        )
        context.user_data.pop(_MODE_KEY, None)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    if not _authorized(update, get_db, owner_id):
        return
    message = update.effective_message
    mode = context.user_data.get(_MODE_KEY)
    if not message or not mode or not message.photo:
        return
    photo = message.photo[-1]
    caption = (message.caption or "").strip()
    if photo.file_size and photo.file_size > _MAX_IMAGE_BYTES:
        await message.reply_text("❌ الصورة أكبر من الحد المسموح به لهذه العملية (20MB).")
        return

    if mode == "teach":
        if not caption:
            await message.reply_text("أرسل الصورة مع وصف قصير لما تريد أن يتعلمه النظام منها.")
            return
        context.user_data[_PENDING_TRAINING_KEY] = {
            "source_file_id": photo.file_id,
            "instruction": caption[:_MAX_PROMPT],
        }
        context.user_data.pop(_MODE_KEY, None)
        await message.reply_text(
            "📚 تم تسجيل صورة المثال.\n\n"
            "الآن أرسل الصورة النهائية/المرجعية التي تمثل النتيجة المطلوبة، وسأحفظ الزوج كبيانات تدريب.",
            reply_markup=_back_keyboard(),
        )
        return

    if mode != "edit":
        return
    if not caption:
        await message.reply_text("❌ اكتب أمر التعديل في تعليق الصورة.")
        return

    job_id = _job_start("edit", caption, os.getenv(_MODEL_ENV, _DEFAULT_MODEL).strip() or _DEFAULT_MODEL)
    context.user_data.pop(_MODE_KEY, None)
    status_message = await message.reply_text("🖼️ جاري تنفيذ التعديل... ⏳")
    temp_path = None
    try:
        telegram_file = await photo.get_file()
        with tempfile.NamedTemporaryFile(prefix="alibot-ai-", suffix=".img", delete=False) as handle:
            temp_path = handle.name
        await telegram_file.download_to_drive(temp_path)
        image_bytes = Path(temp_path).read_bytes()
        mime_type = "image/jpeg"
        output, model = await _run_generation(image_bytes, mime_type, caption)
        if len(output) > 20 * 1024 * 1024:
            raise RuntimeError("IMAGE_AI_OUTPUT_TOO_LARGE")
        _job_finish(job_id, "success")
        await status_message.delete()
        await message.reply_photo(
            photo=output,
            caption=f"🎨 Image AI\n🤖 {model}",
            reply_markup=_back_keyboard(),
        )
    except Exception as exc:
        _job_finish(job_id, "failed", type(exc).__name__)
        logger.exception("Image AI edit failed: %s", type(exc).__name__)
        await status_message.edit_text(
            "❌ تعذر تنفيذ تعديل الصورة.\n\n"
            f"السبب التقني: <code>{type(exc).__name__}</code>\n\n"
            "لم يتم تعديل أي جزء من نظام التنزيل.",
            parse_mode="HTML", reply_markup=_back_keyboard(),
        )
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


async def training_result_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db: Any, owner_id: int) -> None:
    if not _authorized(update, get_db, owner_id):
        return
    message = update.effective_message
    pending = context.user_data.get(_PENDING_TRAINING_KEY)
    if not message or not pending or not message.photo:
        return
    result_photo = message.photo[-1]
    _ensure_schema()
    _save_example(
        pending["source_file_id"],
        result_photo.file_id,
        pending["instruction"],
    )
    context.user_data.pop(_PENDING_TRAINING_KEY, None)
    await message.reply_text(
        "✅ تم حفظ زوج الصورة الأصلية/النتيجة كبيانات تدريب.\n\n"
        "لن يتم تغيير أوزان النموذج أو نسخة الإنتاج تلقائياً.\n"
        "يمكن استخدام هذه البيانات لاحقاً في مرحلة التدريب والتقييم الآمن.",
        reply_markup=_back_keyboard(),
    )


def register_admin_image_ai(app: Any, get_db: Any, owner_id: int) -> None:
    """Register the isolated Image AI Studio exactly once."""
    _ensure_schema()
    app.add_handler(CallbackQueryHandler(
        lambda u, c: ai_studio_callback(u, c, get_db, owner_id),
        pattern=r"^admin_image_ai$",
    ), group=-100)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: edit_start_callback(u, c, get_db, owner_id),
        pattern=r"^admin_image_ai_edit$",
    ), group=-100)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: teach_start_callback(u, c, get_db, owner_id),
        pattern=r"^admin_image_ai_teach$",
    ), group=-100)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: dataset_callback(u, c, get_db, owner_id),
        pattern=r"^admin_image_ai_dataset$",
    ), group=-100)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: jobs_callback(u, c, get_db, owner_id),
        pattern=r"^admin_image_ai_jobs$",
    ), group=-100)
    app.add_handler(CallbackQueryHandler(
        lambda u, c: test_callback(u, c, get_db, owner_id),
        pattern=r"^admin_image_ai_test$",
    ), group=-100)
    app.add_handler(MessageHandler(
        filters.PHOTO & filters.ChatType.PRIVATE,
        lambda u, c: training_result_photo_handler(u, c, get_db, owner_id)
        if c.user_data.get(_PENDING_TRAINING_KEY) else photo_handler(u, c, get_db, owner_id),
    ), group=-150)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        lambda u, c: text_handler(u, c, get_db, owner_id),
    ), group=-150)
