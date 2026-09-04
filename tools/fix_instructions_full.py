from pathlib import Path

PATH = Path("bot.py")
MARKER = "# ALIBOT_INSTRUCTIONS_FULL_V1"

if not PATH.exists():
    raise SystemExit("bot.py not found")

text = PATH.read_text(encoding="utf-8")
if MARKER in text:
    print("AliBot full instructions already fixed")
    raise SystemExit(0)

anchor = '''async def show_instructions_menu(query, language):\n'''
if anchor not in text:
    raise RuntimeError("show_instructions_menu anchor missing")

start = text.index(anchor)
end = text.index('\nasync def instructions_callback', start)

replacement = '''async def show_instructions_menu(query, language):\n    """Show the complete usage guide in one message.\n\n    Navigation uses the dedicated instructions/home callbacks, so this screen\n    never enters the download flow and cannot trigger an expired request.\n    """\n    full_text = (\n        TEXTS[language]["instructions_title"]\n        + "\\n\\n"\n        + TEXTS[language]["instructions_copy_text"]\n        + "\\n\\n━━━━━━━━━━━━━━━━━━\\n\\n"\n        + TEXTS[language]["instructions_share_text"]\n        + "\\n\\n━━━━━━━━━━━━━━━━━━\\n\\n"\n        + TEXTS[language]["instructions_search_text"]\n    )\n\n    keyboard = InlineKeyboardMarkup([\n        [InlineKeyboardButton(TEXTS[language]["instructions_back"], callback_data="main_menu")],\n    ])\n\n    await query.edit_message_text(\n        full_text,\n        reply_markup=keyboard,\n    )\n\n'''

text = text[:start] + replacement + text[end:]
text += "\n" + MARKER + "\n"
PATH.write_text(text, encoding="utf-8")
print("AliBot full instructions fixed successfully")
