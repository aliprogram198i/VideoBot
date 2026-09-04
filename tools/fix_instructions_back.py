from pathlib import Path

PATH = Path("bot.py")
MARKER = "# ALIBOT_INSTRUCTIONS_BACK_V1"

if not PATH.exists():
    raise SystemExit("bot.py not found")

text = PATH.read_text(encoding="utf-8")
if MARKER in text:
    print("AliBot instructions back already fixed")
    raise SystemExit(0)

# The instructions UI currently uses callback_data="main_menu" for its
# return button. That callback was previously handled by download_media,
# which expects a live video_url in context.user_data and therefore returns
# the generic "request expired" message when the user is simply navigating.
# Give navigation its own exact callback handler and leave download logic
# untouched.

function_anchor = '''async def download_media(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE\n):\n'''

if function_anchor not in text:
    raise RuntimeError("download_media function anchor missing")

main_menu_callback = '''async def main_menu_callback(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE\n):\n    query = update.callback_query\n    await query.answer()\n\n    user = update.effective_user\n    register_user(user)\n    language = get_language(user.id) or "ar"\n    await show_main_menu(query, language)\n\n\n'''

text = text.replace(function_anchor, main_menu_callback + function_anchor, 1)

handler_anchor = '''    app.add_handler(\n        CallbackQueryHandler(\n            download_media,\n            pattern=r"^(video_|audio_|main_menu)"\n        )\n    )\n'''

if handler_anchor not in text:
    raise RuntimeError("download_media handler anchor missing")

replacement = '''    app.add_handler(\n        CallbackQueryHandler(\n            main_menu_callback,\n            pattern=r"^main_menu$"\n        )\n    )\n\n    app.add_handler(\n        CallbackQueryHandler(\n            download_media,\n            pattern=r"^(video_|audio_)"\n        )\n    )\n'''

text = text.replace(handler_anchor, replacement, 1)
text += "\n" + MARKER + "\n"
PATH.write_text(text, encoding="utf-8")
print("AliBot instructions back fixed successfully")
