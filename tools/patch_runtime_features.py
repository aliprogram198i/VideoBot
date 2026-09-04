from pathlib import Path

BOT = Path('/app/bot.py')
text = BOT.read_text(encoding='utf-8')

# Install deterministic error capture at startup.
anchor = 'logger = logging.getLogger(__name__)\n'
insert = anchor + '\n# Deterministic admin diagnostics (never uses AI).\ntry:\n    from downloader.error_reporter import install as install_error_reporter\n    install_error_reporter()\nexcept Exception:\n    install_error_reporter = None\n'
if 'from downloader.error_reporter import install as install_error_reporter' not in text:
    if anchor not in text:
        raise SystemExit('logger anchor not found')
    text = text.replace(anchor, insert, 1)

# Add a dedicated current-error button to the existing admin keyboard.
ai_button = '''        [\n            InlineKeyboardButton(\n                "🤖 الذكاء الاصطناعي",\n                callback_data="admin_ai"\n            )\n        ],'''
error_button = ai_button + '''\n        [\n            InlineKeyboardButton(\n                "🧠 تقرير الخطأ الحالي",\n                callback_data="admin_current_error"\n            )\n        ],'''
if 'callback_data="admin_current_error"' not in text:
    if ai_button not in text:
        raise SystemExit('admin AI button anchor not found')
    text = text.replace(ai_button, error_button, 1)

# Add deterministic current-error view before the AI dashboard section.
marker = '# ============================================================\n# الذكاء الاصطناعي - لوحة الإدارة\n# ============================================================\n'
handler = '''# ============================================================\n# تقرير الخطأ الحالي - بدون AI\n# ============================================================\n\nasync def admin_current_error_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):\n    query = update.callback_query\n    await query.answer()\n    if update.effective_user.id != ADMIN_ID:\n        return\n\n    from downloader.error_reporter import latest\n    rows = latest(5)\n    if not rows:\n        text = (\n            "🧠 <b>تقرير الخطأ الحالي</b>\\n"\n            "━━━━━━━━━━━━━━━━━━\\n\\n"\n            "✅ لا توجد أخطاء مسجلة حتى الآن."\n        )\n    else:\n        row = rows[0]\n        created = str(row["created_at"]).replace("T", " ").split(".", 1)[0]\n        text = (\n            "🧠 <b>تقرير الخطأ الحالي</b>\\n"\n            "━━━━━━━━━━━━━━━━━━\\n\\n"\n            f"🔴 <b>السبب الرئيسي:</b> {html.escape(row['reason'])}\\n\\n"\n            f"🏷 <b>التصنيف:</b> <code>{html.escape(row['code'])}</code>\\n"\n            f"🕒 <b>الوقت:</b> {html.escape(created)}\\n"\n        )\n        if row["url"]:\n            text += f"🔗 <b>الرابط:</b> <code>{html.escape(row['url'])}</code>\\n"\n        text += (\n            "\\n📋 <b>آخر الأحداث:</b>\\n"\n            + "\\n".join(\n                f"• {html.escape(str(r['code']))} — {html.escape(str(r['reason']))}"\n                for r in rows[:5]\n            )\n        )\n\n    await query.edit_message_text(\n        text,\n        parse_mode="HTML",\n        reply_markup=InlineKeyboardMarkup([\n            [InlineKeyboardButton("🔄 تحديث التقرير", callback_data="admin_current_error")],\n            [InlineKeyboardButton("🏠 لوحة الإدارة", callback_data="admin_home")],\n        ])\n    )\n\n\n'''
if 'async def admin_current_error_callback' not in text:
    if marker not in text:
        raise SystemExit('AI section marker not found')
    text = text.replace(marker, handler + marker, 1)

# Register callback in the administration section.
registration_anchor = '''    app.add_handler(\n        CallbackQueryHandler(\n            admin_ai_callback,\n            pattern=r"^admin_ai$"\n        )\n    )\n'''
registration = registration_anchor + '''\n    app.add_handler(\n        CallbackQueryHandler(\n            admin_current_error_callback,\n            pattern=r"^admin_current_error$"\n        )\n    )\n'''
if 'pattern=r"^admin_current_error$"' not in text:
    if registration_anchor not in text:
        raise SystemExit('admin AI handler anchor not found')
    text = text.replace(registration_anchor, registration, 1)

BOT.write_text(text, encoding='utf-8')
print('runtime feature patch applied')
