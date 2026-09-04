from pathlib import Path

PATH = Path("bot.py")
MARKER = "# ALIBOT_INSTRUCTIONS_BACK_V2"

if not PATH.exists():
    raise SystemExit("bot.py not found")

text = PATH.read_text(encoding="utf-8")
if MARKER in text:
    print("AliBot instructions navigation already fixed")
    raise SystemExit(0)

# The instructions screen must return to the real home screen, not
# show_main_menu(), which is intentionally the post-link download-type menu.
function_anchor = '''async def download_media(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE\n):\n'''

if function_anchor not in text:
    raise RuntimeError("download_media function anchor missing")

main_menu_callback = '''async def main_menu_callback(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE\n):\n    query = update.callback_query\n    await query.answer()\n\n    user = update.effective_user\n    if not user:\n        return\n\n    register_user(user)\n    language = get_language(user.id) or "ar"\n\n    # Clear only transient download/search state. Persistent user data and\n    # database records are intentionally untouched.\n    context.user_data.pop("video_url", None)\n    context.user_data.pop("smart_search_results", None)\n\n    keyboard = InlineKeyboardMarkup([\n        [InlineKeyboardButton("▶️ ابدأ الآن", callback_data="start_button")],\n        [InlineKeyboardButton(TEXTS[language]["instructions"], callback_data="instructions")],\n    ])\n\n    await query.edit_message_text(\n        TEXTS[language]["welcome"],\n        parse_mode="HTML",\n        reply_markup=keyboard,\n    )\n\n\n'''

text = text.replace(function_anchor, main_menu_callback + function_anchor, 1)

handler_anchor = '''    app.add_handler(\n        CallbackQueryHandler(\n            download_media,\n            pattern=r"^(video_|audio_|main_menu)"\n        )\n    )\n'''

if handler_anchor not in text:
    raise RuntimeError("download_media handler anchor missing")

replacement = '''    app.add_handler(\n        CallbackQueryHandler(\n            main_menu_callback,\n            pattern=r"^main_menu$"\n        )\n    )\n\n    app.add_handler(\n        CallbackQueryHandler(\n            download_media,\n            pattern=r"^(video_|audio_)"\n        )\n    )\n'''

text = text.replace(handler_anchor, replacement, 1)
text += "\n" + MARKER + "\n"
PATH.write_text(text, encoding="utf-8")
print("AliBot instructions navigation fixed successfully")
