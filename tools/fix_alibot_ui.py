from pathlib import Path

PATH = Path("bot.py")
MARKER = "# ALIBOT_UI_V2"

if not PATH.exists():
    raise SystemExit("bot.py not found")

text = PATH.read_text(encoding="utf-8")

if MARKER in text:
    print("AliBot UI already fixed")
    raise SystemExit(0)

# The previous feature patcher creates the instructions callback and text,
# but the original start screen does not expose the button and the callback
# is not registered with python-telegram-bot. Apply only those two missing
# integration points without touching download logic.

start_button = '''                InlineKeyboardButton(\n                    "▶️ ابدأ الآن",\n                    callback_data="start_button"\n                )'''

if start_button not in text:
    raise RuntimeError("start button anchor missing")

start_button_with_instructions = start_button + ''',\n            ],\n            [\n                InlineKeyboardButton(\n                    TEXTS[language]["instructions"],\n                    callback_data="instructions"\n                )'''

text = text.replace(start_button + '\n            ]', start_button_with_instructions, 1)

# Register the instructions callback before the generic download callback.
download_section = '''    # ========================================================\n    # التحميل\n    # ========================================================\n\n    app.add_handler(\n        CallbackQueryHandler(\n            download_media,\n            pattern=r"^(video_|audio_|main_menu)"\n        )\n    )'''

instructions_registration = '''    # ========================================================\n    # تعليمات الاستخدام\n    # ========================================================\n\n    app.add_handler(\n        CallbackQueryHandler(\n            instructions_callback,\n            pattern=r"^instructions(?:_(?:copy|share|search))?$"\n        )\n    )\n\n''' + download_section

if download_section not in text:
    raise RuntimeError("download handler anchor missing")

text = text.replace(download_section, instructions_registration, 1)

text += "\n" + MARKER + "\n"
PATH.write_text(text, encoding="utf-8")
print("AliBot UI integration fixed successfully")
