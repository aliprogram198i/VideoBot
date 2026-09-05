from pathlib import Path

BOT = Path("bot.py")
text = BOT.read_text(encoding="utf-8")
MARKER = "# ADMIN_CONTROL_CENTER_V1"

if MARKER in text:
    print("Admin control center already applied")
    raise SystemExit(0)

# The runtime feature patcher creates the existing admin keyboard.
# Add one entry without replacing or reordering existing controls.
ai_button = '''        [
            InlineKeyboardButton(
                "🤖 الذكاء الاصطناعي",
                callback_data="admin_ai"
            )
        ],'''
control_button = ai_button + '''
        [
            InlineKeyboardButton(
                "🎛️ مركز التحكم الإداري",
                callback_data="admin_control_center"
            )
        ],'''
if 'callback_data="admin_control_center"' not in text:
    if ai_button not in text:
        raise SystemExit("admin keyboard AI anchor not found")
    text = text.replace(ai_button, control_button, 1)

# Register the isolated control-center module after the Application exists.
handler_anchor = '''    # ========================================================
    # الإدارة
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            admin_home_callback,
            pattern=r"^admin_home$"
        )
    )
'''
registration = '''    # ========================================================
    # مركز التحكم الإداري
    # ========================================================

    from plugins.admin_control_center import register_admin_control_center
    register_admin_control_center(app, get_db, ADMIN_ID)

''' + handler_anchor
if 'register_admin_control_center(app, get_db, ADMIN_ID)' not in text:
    if handler_anchor not in text:
        raise SystemExit("admin handler anchor not found")
    text = text.replace(handler_anchor, registration, 1)

text += "\n" + MARKER + "\n"
BOT.write_text(text, encoding="utf-8")
print("Admin control center integration applied successfully")
