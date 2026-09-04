from pathlib import Path
import re
import uuid
from datetime import datetime

PATH = Path("bot.py")
MARKER = "# ALIBOT_FEATURES_V1"

if not PATH.exists():
    raise SystemExit("bot.py not found")

text = PATH.read_text(encoding="utf-8")
if MARKER in text:
    print("AliBot features already applied")
    raise SystemExit(0)

blocks = {
    "ar": '''        "instructions": "📖 تعليمات الاستخدام",
        "instructions_title": "📖 طريقة استخدام AliBot",
        "instructions_copy": "🔗 نسخ الرابط",
        "instructions_share": "📤 مشاركة الرابط",
        "instructions_search": "🔎 البحث بدون رابط",
        "instructions_back": "🔙 العودة",
        "instructions_copy_text": "🔗 نسخ الرابط\\n\\nافتح الفيديو في المنصة، اختر مشاركة/نسخ الرابط ثم أرسله إلى AliBot.\\n\\n✅ سيكتشف البوت المنصة ويعرض خيارات التحميل المناسبة.",
        "instructions_share_text": "📤 مشاركة الرابط\\n\\nمن التطبيق الذي تشاهد فيه الفيديو اضغط مشاركة ثم اختر AliBot إن ظهر ضمن القائمة.\\n\\n✅ لا تحتاج لنسخ الرابط يدوياً إذا ظهر AliBot في قائمة المشاركة.",
        "instructions_search_text": "🔎 البحث بدون رابط\\n\\nاكتب اسم الفيديو أو الأغنية أو الفنان أو الموضوع مباشرة في المحادثة بدون رابط.\\n\\n🤖 سيبحث Smart Search عن أفضل النتائج ويتيح لك اختيار النتيجة المناسبة.\\n",
''',
    "en": '''        "instructions": "📖 How to use",
        "instructions_title": "📖 How to use AliBot",
        "instructions_copy": "🔗 Copy link",
        "instructions_share": "📤 Share link",
        "instructions_search": "🔎 Search without link",
        "instructions_back": "🔙 Back",
        "instructions_copy_text": "🔗 Copy the link\\n\\nOpen the video, choose Share/Copy link, then send it to AliBot.\\n\\n✅ AliBot detects the platform and shows the available download options.",
        "instructions_share_text": "📤 Share the link\\n\\nTap Share in the app and choose AliBot if it appears.\\n\\n✅ No manual copying is needed when AliBot is available in the share sheet.",
        "instructions_search_text": "🔎 Search without a link\\n\\nType the video, song, artist, or topic directly in the chat without a URL.\\n\\n🤖 Smart Search finds the best results for you to choose.",
''',
    "tr": '''        "instructions": "📖 Kullanım talimatları",
        "instructions_title": "📖 AliBot nasıl kullanılır",
        "instructions_copy": "🔗 Bağlantıyı kopyala",
        "instructions_share": "📤 Bağlantıyı paylaş",
        "instructions_search": "🔎 Bağlantısız ara",
        "instructions_back": "🔙 Geri",
        "instructions_copy_text": "🔗 Bağlantıyı kopyala\\n\\nVideoyu açın, Paylaş/Bağlantıyı kopyala seçeneğini kullanın ve AliBot'a gönderin.\\n\\n✅ AliBot platformu algılar ve uygun seçenekleri gösterir.",
        "instructions_share_text": "📤 Bağlantıyı paylaş\\n\\nPaylaş'a dokunun ve listede AliBot varsa seçin.\\n\\n✅ AliBot paylaşım menüsündeyse bağlantıyı elle kopyalamanız gerekmez.",
        "instructions_search_text": "🔎 Bağlantısız ara\\n\\nURL göndermeden video, şarkı, sanatçı veya konu adını yazın.\\n\\n🤖 Smart Search en iyi sonuçları bulur.",
''',
    "de": '''        "instructions": "📖 Anleitung",
        "instructions_title": "📖 AliBot verwenden",
        "instructions_copy": "🔗 Link kopieren",
        "instructions_share": "📤 Link teilen",
        "instructions_search": "🔎 Ohne Link suchen",
        "instructions_back": "🔙 Zurück",
        "instructions_copy_text": "🔗 Link kopieren\\n\\nÖffnen Sie das Video, wählen Sie Teilen/Link kopieren und senden Sie den Link an AliBot.\\n\\n✅ AliBot erkennt die Plattform und zeigt die verfügbaren Optionen.",
        "instructions_share_text": "📤 Link teilen\\n\\nTippen Sie auf Teilen und wählen Sie AliBot, falls verfügbar.\\n\\n✅ Kein manuelles Kopieren nötig, wenn AliBot im Teilen-Menü erscheint.",
        "instructions_search_text": "🔎 Ohne Link suchen\\n\\nSchreiben Sie den Namen des Videos, Songs, Künstlers oder Themas direkt in den Chat.\\n\\n🤖 Smart Search findet die besten Ergebnisse.",
''',
}

for lang, block in blocks.items():
    start = text.find(f'    "{lang}": {{')
    if start < 0:
        raise RuntimeError(f"language block missing: {lang}")
    end = text.find('\n    "share":', start)
    if end < 0:
        raise RuntimeError(f"share key missing: {lang}")
    text = text[:end] + '\n' + block.rstrip('\n') + text[end:]

# Database: non-destructive pending invitations table.
anchor = '''    conn.commit()\n    conn.close()\n\n    print("✅ Database initialized successfully.")\n'''
replacement = '''    cur.execute("""\n        CREATE TABLE IF NOT EXISTS pending_user_invites (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            username TEXT NOT NULL,\n            token TEXT NOT NULL UNIQUE,\n            admin_id INTEGER NOT NULL,\n            status TEXT NOT NULL DEFAULT 'pending',\n            user_id INTEGER,\n            created_at TEXT NOT NULL,\n            completed_at TEXT\n        )\n    """)\n\n    cur.execute("""\n        CREATE INDEX IF NOT EXISTS idx_pending_invites_token\n        ON pending_user_invites(token)\n    """)\n\n    conn.commit()\n    conn.close()\n\n    print("✅ Database initialized successfully.")\n'''
if anchor not in text:
    raise RuntimeError("init_db anchor missing")
text = text.replace(anchor, replacement, 1)

# Helpers before register_user.
anchor = '''# ============================================================\n# تسجيل المستخدم\n# ============================================================\n\ndef register_user(user):\n'''
helpers = '''# ============================================================\n# إضافة مستخدم آمنة عبر username\n# ============================================================\n\ndef normalize_telegram_username(value):\n    value = (value or "").strip()\n    return value[1:].strip().lower() if value.startswith("@") else value.lower()\n\ndef create_pending_user_invite(username, admin_id):\n    username = normalize_telegram_username(username)\n    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):\n        raise ValueError("Invalid Telegram username")\n    conn = get_db()\n    cur = conn.cursor()\n    cur.execute("SELECT user_id FROM users WHERE lower(username)=? LIMIT 1", (username,))\n    existing = cur.fetchone()\n    if existing:\n        conn.close()\n        return {"status": "existing", "user_id": existing["user_id"]}\n    token = uuid.uuid4().hex\n    cur.execute("""\n        INSERT INTO pending_user_invites (username, token, admin_id, status, created_at)\n        VALUES (?, ?, ?, 'pending', ?)\n    """, (username, token, admin_id, datetime.now().isoformat()))\n    conn.commit()\n    conn.close()\n    return {"status": "pending", "username": username, "token": token}\n\ndef complete_pending_user_invite(token, user):\n    if not token or not user:\n        return False\n    conn = get_db()\n    cur = conn.cursor()\n    cur.execute("SELECT id, username FROM pending_user_invites WHERE token=? AND status='pending' LIMIT 1", (token,))\n    invite = cur.fetchone()\n    if not invite or normalize_telegram_username(user.username) != normalize_telegram_username(invite["username"]):\n        conn.close()\n        return False\n    cur.execute("UPDATE pending_user_invites SET status='completed', user_id=?, completed_at=? WHERE id=?", (user.id, datetime.now().isoformat(), invite["id"]))\n    conn.commit()\n    conn.close()\n    return True\n\n\n# ============================================================\n# تسجيل المستخدم\n# ============================================================\n\ndef register_user(user):\n'''
if anchor not in text:
    raise RuntimeError("register_user anchor missing")
text = text.replace(anchor, helpers, 1)

# Instructions before video menu.
anchor = '''async def show_video_menu(\n    query,\n    language\n):\n'''
ui = '''async def show_instructions_menu(query, language):\n    keyboard = InlineKeyboardMarkup([\n        [InlineKeyboardButton(TEXTS[language]["instructions_copy"], callback_data="instructions_copy")],\n        [InlineKeyboardButton(TEXTS[language]["instructions_share"], callback_data="instructions_share")],\n        [InlineKeyboardButton(TEXTS[language]["instructions_search"], callback_data="instructions_search")],\n        [InlineKeyboardButton(TEXTS[language]["instructions_back"], callback_data="main_menu")],\n    ])\n    await query.edit_message_text(TEXTS[language]["instructions_title"], reply_markup=keyboard)\n\nasync def instructions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):\n    query = update.callback_query\n    await query.answer()\n    user = update.effective_user\n    register_user(user)\n    language = get_language(user.id) or "ar"\n    if query.data == "instructions":\n        await show_instructions_menu(query, language)\n        return\n    messages = {\n        "instructions_copy": TEXTS[language]["instructions_copy_text"],\n        "instructions_share": TEXTS[language]["instructions_share_text"],\n        "instructions_search": TEXTS[language]["instructions_search_text"],\n    }\n    if query.data in messages:\n        await query.edit_message_text(messages[query.data], reply_markup=InlineKeyboardMarkup([\n            [InlineKeyboardButton(TEXTS[language]["instructions_back"], callback_data="instructions")],\n            [InlineKeyboardButton(TEXTS[language]["instructions_back"], callback_data="main_menu")],\n        ]))\n\n\n'''
if anchor not in text:
    raise RuntimeError("show_video_menu anchor missing")
text = text.replace(anchor, ui + anchor, 1)

# Add permanent instructions button to the current main menu.
start = text.find('async def show_main_menu(')
end = text.find('async def show_video_menu(', start)
segment = text[start:end]
render = segment.find('    await query.edit_message_text(')
close = segment[:render].rfind('    ])')
if start < 0 or end < 0 or render < 0 or close < 0:
    raise RuntimeError("main menu structure not found")
segment = segment[:close] + '        [InlineKeyboardButton(TEXTS[language]["instructions"], callback_data="instructions")],\n\n' + segment[close:]
text = text[:start] + segment + text[end:]

# Complete invite token on /start after the existing register_user call.
start = text.find('async def start(')
end = text.find('\nasync def ', start + 1)
segment = text[start:end]
needle = '    register_user(user)\n'
pos = segment.find(needle)
if start < 0 or end < 0 or pos < 0:
    raise RuntimeError("start function structure not found")
segment = segment[:pos] + needle + '''\n    start_args = context.args or []\n    if start_args and start_args[0].startswith("add_"):\n        complete_pending_user_invite(start_args[0][4:], user)\n''' + segment[pos + len(needle):]
text = text[:start] + segment + text[end:]

# Admin add-user flow before details.
anchor = '''async def admin_user_details(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE\n):\n'''
flow = '''async def admin_add_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):\n    query = update.callback_query\n    await query.answer()\n    if update.effective_user.id != ADMIN_ID:\n        return\n    context.user_data["waiting_admin_add_user"] = True\n    await query.edit_message_text(\n        "➕ إضافة مستخدم للبوت\\n\\nأرسل Telegram username للمستخدم.\\nمثال: @username\\n\\n🔒 لن يتم اختلاق Telegram ID أو إنشاء حساب وهمي. إذا كان المستخدم معروفاً سيُفتح حسابه مباشرة، وإلا سيُنشأ رابط دعوة آمن لربط الحساب عند فتح البوت."\n    )\n\nasync def process_admin_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):\n    if update.effective_user.id != ADMIN_ID or not context.user_data.get("waiting_admin_add_user"):\n        return\n    context.user_data["waiting_admin_add_user"] = False\n    username = normalize_telegram_username(update.message.text)\n    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):\n        await update.message.reply_text("❌ Telegram username غير صالح. استخدم @username")\n        return\n    result = create_pending_user_invite(username, ADMIN_ID)\n    if result["status"] == "existing":\n        await update.message.reply_text("✅ المستخدم موجود بالفعل.\\n\\n🆔 Telegram ID: " + str(result["user_id"]), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 عرض المستخدم", callback_data=f"user_{result['user_id']}")]]))\n        return\n    bot_username = context.bot.username\n    link = f"https://t.me/{bot_username}?start=add_{result['token']}"\n    await update.message.reply_text("✅ تم إنشاء دعوة آمنة.\\n\\n👤 username: @" + result["username"] + "\\n⏳ الحالة: بانتظار فتح البوت\\n\\n📎 أرسل هذا الرابط للمستخدم:\\n" + link + "\\n\\nسيتم ربط Telegram ID الحقيقي عند فتح الرابط، دون تعديل البيانات الموجودة.")\n\n\n'''
if anchor not in text:
    raise RuntimeError("admin_user_details anchor missing")
text = text.replace(anchor, flow + anchor, 1)

# Users button.
anchor = '''    keyboard.append([\n\n        InlineKeyboardButton(\n            "🔍 بحث عن مستخدم",\n            callback_data="admin_search"\n        )\n\n    ])\n'''
replacement = '''    keyboard.append([InlineKeyboardButton("➕ إضافة مستخدم", callback_data="admin_add_user")])\n\n''' + anchor
if anchor not in text:
    raise RuntimeError("admin search button anchor missing")
text = text.replace(anchor, replacement, 1)

# Admin text router.
anchor = '''    if context.user_data.get(\n        "waiting_admin_search"\n    ):\n'''
replacement = '''    if context.user_data.get(\n        "waiting_admin_add_user"\n    ):\n        await process_admin_add_user(update, context)\n        return\n\n    if context.user_data.get(\n        "waiting_admin_search"\n    ):\n'''
if anchor not in text:
    raise RuntimeError("admin router anchor missing")
text = text.replace(anchor, replacement, 1)

# Handlers.
anchor = '''    app.add_handler(\n        CallbackQueryHandler(\n            admin_search_callback,\n            pattern=r"^admin_search$"\n        )\n    )\n'''
handler = '''    app.add_handler(CallbackQueryHandler(instructions_callback, pattern=r"^instructions(?:_(?:copy|share|search))?$"))\n\n    app.add_handler(CallbackQueryHandler(admin_add_user_callback, pattern=r"^admin_add_user$"))\n\n''' + anchor
if anchor not in text:
    raise RuntimeError("admin search handler anchor missing")
text = text.replace(anchor, handler, 1)

PATH.write_text(MARKER + "\n" + text, encoding="utf-8")
print("AliBot features applied successfully")
