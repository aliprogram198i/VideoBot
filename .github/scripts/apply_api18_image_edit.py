from pathlib import Path

path = Path("bot.py")
text = path.read_text(encoding="utf-8")
MARKER = "ALIBOT_API18_IMAGE_EDIT_V1"

if MARKER in text:
    print("Api18 image editor already applied.")
    raise SystemExit(0)

# 1) Imports.
anchor = "import uuid\n"
replacement = (
    "import uuid\n"
    "import mimetypes\n"
    "import threading\n"
    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
    "import httpx\n"
)
if anchor not in text:
    raise RuntimeError("Import anchor not found")
text = text.replace(anchor, replacement, 1)

# 2) Isolated Api18 integration. It never touches downloader logic.
anchor = "# ============================================================\n# النصوص\n# ============================================================\n"
section = r'''# ============================================================
# Api18.dev AI Image Editor - admin only
# ALIBOT_API18_IMAGE_EDIT_V1
# ============================================================

API18_IMAGE_EDIT_KEY = os.getenv("Aswlt_crem")
AI_IMAGE_PUBLIC_BASE_URL = os.getenv("AI_IMAGE_PUBLIC_BASE_URL", "").rstrip("/")
AI_IMAGE_SERVER_PORT = int(os.getenv("PORT", "8080"))
AI_IMAGE_MAX_BYTES = 10 * 1024 * 1024
AI_IMAGE_REQUEST_TIMEOUT = 180
AI_IMAGE_POLL_INTERVAL = 2
AI_IMAGE_POLL_TIMEOUT = 120

_ai_image_files = {}
_ai_image_files_lock = threading.Lock()
_ai_image_server = None


def _ai_image_token():
    return uuid.uuid4().hex + uuid.uuid4().hex


def _ai_image_register_file(file_path):
    token = _ai_image_token()
    with _ai_image_files_lock:
        _ai_image_files[token] = os.path.realpath(file_path)
    return token


def _ai_image_remove_token(token):
    with _ai_image_files_lock:
        _ai_image_files.pop(token, None)


class _AIImageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        prefix = "/ai-image/"
        if not self.path.startswith(prefix):
            self.send_error(404)
            return

        token = self.path[len(prefix):].split("?", 1)[0]
        if not re.fullmatch(r"[a-f0-9]{64}", token):
            self.send_error(404)
            return

        with _ai_image_files_lock:
            file_path = _ai_image_files.get(token)

        if not file_path or not os.path.isfile(file_path):
            self.send_error(404)
            return

        try:
            size = os.path.getsize(file_path)
            if size <= 0 or size > AI_IMAGE_MAX_BYTES:
                self.send_error(404)
                return

            content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.end_headers()

            with open(file_path, "rb") as source:
                while True:
                    chunk = source.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            try:
                self.send_error(500)
            except Exception:
                pass

    def log_message(self, format, *args):
        return


def start_ai_image_server():
    global _ai_image_server
    if _ai_image_server is not None:
        return
    if not AI_IMAGE_PUBLIC_BASE_URL:
        logger.warning("AI image editor disabled: AI_IMAGE_PUBLIC_BASE_URL is not configured")
        return

    try:
        _ai_image_server = ThreadingHTTPServer(("0.0.0.0", AI_IMAGE_SERVER_PORT), _AIImageHandler)
        thread = threading.Thread(
            target=_ai_image_server.serve_forever,
            name="ai-image-server",
            daemon=True,
        )
        thread.start()
        print("🖼️ Api18 AI image editor: HTTP endpoint ready")
    except Exception as exc:
        _ai_image_server = None
        logger.warning("AI image server failed to start: %s", type(exc).__name__)


async def api18_edit_image(image_path, prompt):
    if not API18_IMAGE_EDIT_KEY:
        raise RuntimeError("Aswlt_crem is not configured")
    if not AI_IMAGE_PUBLIC_BASE_URL:
        raise RuntimeError("AI_IMAGE_PUBLIC_BASE_URL is not configured")
    if not os.path.isfile(image_path):
        raise FileNotFoundError("Reference image was not found")
    if os.path.getsize(image_path) > AI_IMAGE_MAX_BYTES:
        raise ValueError("Image exceeds the 10 MB Api18 limit")

    token = _ai_image_register_file(image_path)
    image_url = f"{AI_IMAGE_PUBLIC_BASE_URL}/ai-image/{token}"
    result_path = image_path + ".result"

    try:
        headers = {
            "Authorization": f"Bearer {API18_IMAGE_EDIT_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "hera-1.0-image-edit",
            "prompt": prompt[:2500],
            "images": [image_url],
        }

        timeout = httpx.Timeout(AI_IMAGE_REQUEST_TIMEOUT, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                "https://api18.dev/v1/generate?wait=true",
                headers=headers,
                json=payload,
            )

            if response.status_code not in (200, 202):
                try:
                    data = response.json()
                    error = data.get("error", {}) if isinstance(data, dict) else {}
                    message = error.get("message") or f"Api18 HTTP {response.status_code}"
                except Exception:
                    message = f"Api18 HTTP {response.status_code}"
                raise RuntimeError(message[:500])

            data = response.json()
            output_url = None
            job_id = None
            status = None
            if isinstance(data, dict):
                items = data.get("data") or []
                if items and isinstance(items[0], dict):
                    output_url = items[0].get("url")
                job_id = data.get("id")
                status = data.get("status")

            if not output_url and job_id and status in ("processing", None):
                deadline = time.monotonic() + AI_IMAGE_POLL_TIMEOUT
                while time.monotonic() < deadline:
                    await asyncio.sleep(AI_IMAGE_POLL_INTERVAL)
                    job_response = await client.get(
                        f"https://api18.dev/v1/jobs/{job_id}",
                        headers={"Authorization": f"Bearer {API18_IMAGE_EDIT_KEY}"},
                    )
                    if job_response.status_code != 200:
                        continue
                    job = job_response.json()
                    status = job.get("status") if isinstance(job, dict) else None
                    items = job.get("data") or [] if isinstance(job, dict) else []
                    if items and isinstance(items[0], dict):
                        output_url = items[0].get("url")
                    if status == "completed" and output_url:
                        break
                    if status == "failed":
                        error = job.get("error") or "Api18 image edit failed"
                        raise RuntimeError(str(error)[:500])

            if not output_url:
                raise RuntimeError("Api18 returned no image URL")

            parsed = urlparse(output_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise RuntimeError("Api18 returned an invalid output URL")

            # Api18's file endpoint may redirect to its storage object, so follow redirects only for
            # the provider-supplied output URL, while keeping the API request itself non-redirecting.
            async with client.stream("GET", output_url, follow_redirects=True) as result_response:
                if result_response.status_code != 200:
                    raise RuntimeError(
                        f"Api18 output download failed: HTTP {result_response.status_code}"
                    )
                content_length = result_response.headers.get("Content-Length")
                if content_length and int(content_length) > AI_IMAGE_MAX_BYTES:
                    raise RuntimeError("Generated image exceeds the 10 MB limit")
                total = 0
                with open(result_path, "wb") as output:
                    async for chunk in result_response.aiter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > AI_IMAGE_MAX_BYTES:
                            raise RuntimeError("Generated image exceeds the 10 MB limit")
                        output.write(chunk)

            if not os.path.isfile(result_path) or os.path.getsize(result_path) == 0:
                raise RuntimeError("Api18 returned an empty image")
            return result_path
    finally:
        _ai_image_remove_token(token)


async def admin_ai_image_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return

    if not API18_IMAGE_EDIT_KEY or not AI_IMAGE_PUBLIC_BASE_URL:
        await query.edit_message_text(
            "🖼️ <b>تعديل الصورة بالذكاء الاصطناعي</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ الميزة غير مهيأة بعد.\n\n"
            "يجب ضبط متغيري Railway:\n"
            "• <code>Aswlt_crem</code>\n"
            "• <code>AI_IMAGE_PUBLIC_BASE_URL</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 الذكاء الاصطناعي", callback_data="admin_ai")],
                [InlineKeyboardButton("🏠 لوحة الإدارة", callback_data="admin_home")],
            ]),
        )
        return

    context.user_data["waiting_ai_image"] = True
    context.user_data.pop("waiting_ai_prompt", None)
    context.user_data.pop("ai_image_path", None)
    context.user_data.pop("ai_image_dir", None)
    await query.edit_message_text(
        "🖼️ <b>تعديل الصورة بالذكاء الاصطناعي</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📤 أرسل الآن الصورة التي تريد تعديلها.\n\n"
        "بعد إرسال الصورة سأطلب منك وصف التعديل المطلوب.\n\n"
        "❌ للإلغاء: /cancel",
        parse_mode="HTML",
    )


async def admin_ai_image_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message or not update.message.photo:
        return
    if not context.user_data.get("waiting_ai_image"):
        return

    photo = update.message.photo[-1]
    temp_dir = tempfile.mkdtemp(prefix="videobot_ai_")
    image_path = os.path.join(temp_dir, "input.jpg")
    try:
        telegram_file = await context.bot.get_file(photo.file_id)
        await telegram_file.download_to_drive(image_path)
        if os.path.getsize(image_path) > AI_IMAGE_MAX_BYTES:
            shutil.rmtree(temp_dir, ignore_errors=True)
            await update.message.reply_text("❌ الصورة تتجاوز الحد الأقصى المسموح به (10 MB).")
            return
        context.user_data["ai_image_path"] = image_path
        context.user_data["ai_image_dir"] = temp_dir
        context.user_data["waiting_ai_image"] = False
        context.user_data["waiting_ai_prompt"] = True
        await update.message.reply_text(
            "✅ تم استلام الصورة.\n\n"
            "✏️ اكتب الآن وصف التعديل المطلوب.\n\n"
            "مثال:\n"
            "<code>غيّر الخلفية إلى مدينة ليلية مع الحفاظ على الشخص كما هو.</code>\n\n"
            "❌ للإلغاء: /cancel",
            parse_mode="HTML",
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await update.message.reply_text("❌ تعذر استلام الصورة. حاول مرة أخرى.")


async def admin_ai_image_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message:
        return
    if not context.user_data.get("waiting_ai_prompt"):
        return

    prompt = (update.message.text or "").strip()
    if not prompt:
        await update.message.reply_text("❌ اكتب وصف التعديل أولاً.")
        return
    if len(prompt) > 2500:
        await update.message.reply_text("❌ وصف التعديل طويل جداً. الحد الأقصى 2500 حرف.")
        return

    image_path = context.user_data.get("ai_image_path")
    image_dir = context.user_data.get("ai_image_dir")
    context.user_data["waiting_ai_prompt"] = False

    if not image_path or not image_dir or not os.path.isfile(image_path):
        context.user_data.clear()
        await update.message.reply_text("❌ انتهت صلاحية الصورة. ابدأ العملية من جديد.")
        return

    status = await update.message.reply_text(
        "⏳ <b>جاري تعديل الصورة...</b>\n\n"
        "🤖 المحرك: Hera 1.0 Image Edit\n"
        "⚙️ تتم المعالجة الآن، يرجى الانتظار...",
        parse_mode="HTML",
    )

    result_path = None
    try:
        result_path = await api18_edit_image(image_path, prompt)
        with open(result_path, "rb") as result_file:
            await update.message.reply_photo(
                photo=result_file,
                caption="✅ <b>تم تعديل الصورة بنجاح!</b>\n\n🖼️ تم تنفيذ التعديل عبر Api18.dev.",
                parse_mode="HTML",
            )
        await status.edit_text(
            "✅ <b>اكتمل تعديل الصورة بنجاح.</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🖼️ تعديل صورة أخرى", callback_data="admin_ai_image")],
                [InlineKeyboardButton("🔙 الذكاء الاصطناعي", callback_data="admin_ai")],
                [InlineKeyboardButton("🏠 لوحة الإدارة", callback_data="admin_home")],
            ]),
        )
    except Exception as exc:
        logger.warning("Api18 image edit failed: %s", type(exc).__name__)
        await status.edit_text(
            "❌ <b>تعذر تعديل الصورة.</b>\n\n"
            f"نوع الخطأ: <code>{html.escape(type(exc).__name__)}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 المحاولة مرة أخرى", callback_data="admin_ai_image")],
                [InlineKeyboardButton("🔙 الذكاء الاصطناعي", callback_data="admin_ai")],
            ]),
        )
    finally:
        context.user_data.pop("ai_image_path", None)
        context.user_data.pop("ai_image_dir", None)
        context.user_data.pop("waiting_ai_image", None)
        context.user_data.pop("waiting_ai_prompt", None)
        shutil.rmtree(image_dir, ignore_errors=True)
        if result_path and os.path.exists(result_path):
            try:
                os.remove(result_path)
            except OSError:
                pass


'''
if anchor not in text:
    raise RuntimeError("Text section anchor not found")
text = text.replace(anchor, section + anchor, 1)

# 3) Add image-edit button to the existing Gemini admin menu.
anchor = '''        [
            InlineKeyboardButton(
                "🐞 تقرير الأخطاء",
                callback_data="ai_errors"
            )
        ],
'''
replacement = anchor + '''        [
            InlineKeyboardButton(
                "🖼️ تعديل صورة بالذكاء الاصطناعي",
                callback_data="admin_ai_image"
            )
        ],
'''
if anchor not in text:
    raise RuntimeError("Admin AI menu anchor not found")
text = text.replace(anchor, replacement, 1)

# 4) Register the photo handler. Prompt handling is integrated into the existing admin text router,
#    so ordinary admin text continues to behave exactly as before.
anchor = '''    app.add_handler(
        CallbackQueryHandler(
            admin_storage_callback,
            pattern=r"^admin_storage$"
        )
    )
'''
replacement = anchor + '''
    app.add_handler(
        CallbackQueryHandler(
            admin_ai_image_menu_callback,
            pattern=r"^admin_ai_image$"
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.User(ADMIN_ID),
            admin_ai_image_photo_callback
        )
    )
'''
if anchor not in text:
    raise RuntimeError("Admin storage handler anchor not found")
text = text.replace(anchor, replacement, 1)

# 5) Route the AI prompt through the already-existing admin text router.
anchor = '''    if context.user_data.get(
        "waiting_broadcast"
    ):
'''
replacement = '''    if context.user_data.get(
        "waiting_ai_prompt"
    ):

        await admin_ai_image_prompt_callback(
            update,
            context
        )

        return

    if context.user_data.get(
        "waiting_broadcast"
    ):
'''
router_pos = text.find("async def admin_text_router(")
if router_pos < 0:
    raise RuntimeError("Admin text router not found")
local_pos = text.find(anchor, router_pos)
if local_pos < 0:
    raise RuntimeError("Admin text router anchor not found")
text = text[:local_pos] + replacement + text[local_pos + len(anchor):]

# 6) Start the tiny public endpoint after Application creation and before polling.
anchor = '''    app = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .build()
    )
'''
replacement = anchor + '''
    start_ai_image_server()
'''
if anchor not in text:
    raise RuntimeError("Application build anchor not found")
text = text.replace(anchor, replacement, 1)

# 7) Clean AI temporary state on /cancel without changing existing cancellation behavior.
anchor = '''async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()


    await update.message.reply_text("✅ تم إلغاء العملية.")
'''
replacement = '''async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    ai_image_dir = context.user_data.get("ai_image_dir")
    context.user_data.clear()
    if ai_image_dir:
        shutil.rmtree(ai_image_dir, ignore_errors=True)

    await update.message.reply_text("✅ تم إلغاء العملية.")
'''
if anchor not in text:
    raise RuntimeError("Cancel command anchor not found")
text = text.replace(anchor, replacement, 1)

path.write_text(text, encoding="utf-8")
print("Api18 image editor patch applied successfully.")
