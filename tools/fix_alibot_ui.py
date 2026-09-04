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

start_block = '''            [
                InlineKeyboardButton(
                    "▶️ ابدأ الآن",
                    callback_data="start_button"
                )
            ]
        ])'''

replacement = '''            [
                InlineKeyboardButton(
                    "▶️ ابدأ الآن",
                    callback_data="start_button"
                )
            ],
            [
                InlineKeyboardButton(
                    TEXTS[language]["instructions"],
                    callback_data="instructions"
                )
            ]
        ])'''

if start_block not in text:
    raise RuntimeError("start screen keyboard anchor missing")

text = text.replace(start_block, replacement, 1)

# Register the instructions callback before the generic download callback.
download_section = '''    # ========================================================
    # التحميل
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            download_media,
            pattern=r"^(video_|audio_|main_menu)"
        )
    )'''

instructions_registration = '''    # ========================================================
    # تعليمات الاستخدام
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            instructions_callback,
            pattern=r"^instructions(?:_(?:copy|share|search))?$"
        )
    )

''' + download_section

if download_section not in text:
    raise RuntimeError("download handler anchor missing")

text = text.replace(download_section, instructions_registration, 1)

text += "\n" + MARKER + "\n"
PATH.write_text(text, encoding="utf-8")
print("AliBot UI integration fixed successfully")
