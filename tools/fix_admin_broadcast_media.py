from pathlib import Path

PATH = Path("bot.py")
MARKER = "# ALIBOT_BROADCAST_MEDIA_V1"

if not PATH.exists():
    raise SystemExit("bot.py not found")

text = PATH.read_text(encoding="utf-8")
if MARKER in text:
    print("AliBot media broadcast already fixed")
    raise SystemExit(0)

# ------------------------------------------------------------
# Broadcast media sender
# ------------------------------------------------------------
anchor = '''async def process_broadcast(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE\n):\n'''
if anchor not in text:
    raise RuntimeError("process_broadcast anchor missing")

media_sender = '''async def process_broadcast_media(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE\n):\n    """Broadcast one admin-supplied Telegram media message safely.\n\n    The bot copies the original Telegram message instead of downloading and\n    re-uploading the media. This preserves the media type/caption and avoids\n    temporary files and unnecessary bandwidth on Railway.\n    """\n    if not update.message or update.effective_user.id != ADMIN_ID:\n        return\n\n    if not context.user_data.get("waiting_broadcast"):\n        return\n\n    message = update.message\n\n    if message.photo:\n        media_kind = "photo"\n    elif message.video:\n        media_kind = "video"\n    elif message.audio:\n        media_kind = "audio"\n    elif message.voice:\n        media_kind = "voice"\n    else:\n        await update.message.reply_text(\n            "❌ نوع الوسائط غير مدعوم.\\n\\n"\n            "أرسل صورة أو فيديو أو ملف صوتي أو رسالة صوتية، "\n            "أو أرسل إعلانًا نصيًا."\n        )\n        return\n\n    caption = message.caption or ""\n    if len(caption) > 1024:\n        await update.message.reply_text(\n            "❌ وصف الإعلان طويل جدًا. الحد الأقصى 1024 حرفًا."\n        )\n        return\n\n    context.user_data["waiting_broadcast"] = False\n\n    conn = get_db()\n    cur = conn.cursor()\n    log_message = f"[MEDIA:{media_kind}]"\n    if caption:\n        log_message += " " + caption[:1000]\n    cur.execute("""\n        INSERT INTO broadcast_logs (\n            admin_id, message, sent_count, failed_count, created_at\n        )\n        VALUES (?, ?, 0, 0, ?)\n    """, (ADMIN_ID, log_message, datetime.now().isoformat()))\n    broadcast_id = cur.lastrowid\n\n    cur.execute("""\n        SELECT user_id\n        FROM users\n        WHERE is_banned = 0\n    """)\n    users = cur.fetchall()\n    conn.commit()\n    conn.close()\n\n    status_message = await update.message.reply_text(\n        "📢 جاري إرسال الإعلان المرئي...\\n\\n"\n        f"📦 النوع: {media_kind}\\n"\n        f"👥 المستهدفون: {len(users)}\\n\\n"\n        "⏳ بدأ الإرسال..."\n    )\n\n    sent = 0\n    failed = 0\n\n    for index, row in enumerate(users, start=1):\n        target_id = row["user_id"]\n\n        try:\n            # copy_message works for photo/video/audio/voice and keeps the\n            # original media without creating a local temporary file.\n            sent_message = await context.bot.copy_message(\n                chat_id=target_id,\n                from_chat_id=update.effective_chat.id,\n                message_id=message.message_id,\n            )\n\n            conn_save = get_db()\n            cur_save = conn_save.cursor()\n            cur_save.execute("""\n                INSERT INTO broadcast_messages (\n                    broadcast_id, user_id, message_id, created_at\n                )\n                VALUES (?, ?, ?, ?)\n            """, (\n                broadcast_id,\n                target_id,\n                sent_message.message_id,\n                datetime.now().isoformat(),\n            ))\n            conn_save.commit()\n            conn_save.close()\n\n            sent += 1\n\n        except Exception as exc:\n            # Respect Telegram flood-wait responses when exposed by PTB.\n            retry_after = getattr(exc, "retry_after", None)\n            if retry_after is not None:\n                try:\n                    await asyncio.sleep(min(float(retry_after), 60.0))\n                    sent_message = await context.bot.copy_message(\n                        chat_id=target_id,\n                        from_chat_id=update.effective_chat.id,\n                        message_id=message.message_id,\n                    )\n                    conn_save = get_db()\n                    cur_save = conn_save.cursor()\n                    cur_save.execute("""\n                        INSERT INTO broadcast_messages (\n                            broadcast_id, user_id, message_id, created_at\n                        )\n                        VALUES (?, ?, ?, ?)\n                    """, (\n                        broadcast_id,\n                        target_id,\n                        sent_message.message_id,\n                        datetime.now().isoformat(),\n                    ))\n                    conn_save.commit()\n                    conn_save.close()\n                    sent += 1\n                    await asyncio.sleep(0.05)\n                    continue\n                except Exception as retry_exc:\n                    exc = retry_exc\n\n            failed += 1\n            print(\n                f"Broadcast media error {target_id}: "\n                f"{type(exc).__name__}"\n            )\n\n        # Keep the free broadcast rate comfortably below Telegram's normal\n        # 30 messages/second limit and avoid hammering the API.\n        await asyncio.sleep(0.05)\n\n        if index % 25 == 0 or index == len(users):\n            try:\n                await status_message.edit_text(\n                    "📢 جاري إرسال الإعلان المرئي...\\n\\n"\n                    f"📦 النوع: {media_kind}\\n"\n                    f"📨 تم الإرسال: {sent}\\n"\n                    f"❌ فشل: {failed}\\n"\n                    f"👥 تمت المعالجة: {index}/{len(users)}"\n                )\n            except Exception:\n                pass\n\n    conn = get_db()\n    cur = conn.cursor()\n    cur.execute("""\n        UPDATE broadcast_logs\n        SET sent_count = ?, failed_count = ?\n        WHERE id = ?\n    """, (sent, failed, broadcast_id))\n    conn.commit()\n    conn.close()\n\n    await status_message.edit_text(\n        "✅ انتهى إرسال الإعلان المرئي.\\n\\n"\n        f"📦 النوع: {media_kind}\\n"\n        f"📨 تم الإرسال: {sent}\\n"\n        f"❌ فشل الإرسال: {failed}\\n"\n        f"👥 الإجمالي: {len(users)}"\n    )\n\n\n'''
text = text.replace(anchor, media_sender + anchor, 1)

# Make the existing admin broadcast prompt explicitly accept text or media.
old_prompt = '''        "📢 إرسال إعلان\\n\\n"\n        "أرسل الآن نص الإعلان.\\n\\n"\n        "سيتم إرساله إلى جميع المستخدمين "\n        "غير المحظورين.\\n\\n"\n        "❌ للإلغاء استخدم /cancel"'''
new_prompt = '''        "📢 إرسال إعلان\\n\\n"\n        "أرسل الآن أحد الأنواع التالية:\\n"\n        "• 📝 نص\\n"\n        "• 🖼️ صورة\\n"\n        "• 🎬 فيديو\\n"\n        "• 🎵 صوت / ملف صوتي\\n"\n        "• 🎙️ رسالة صوتية\\n\\n"\n        "يمكنك إضافة وصف (Caption) مع الوسائط.\\n"\n        "سيتم الإرسال إلى جميع المستخدمين غير المحظورين.\\n\\n"\n        "❌ للإلغاء استخدم /cancel"'''
if old_prompt not in text:
    raise RuntimeError("admin broadcast prompt anchor missing")
text = text.replace(old_prompt, new_prompt, 1)

# The command prompt should match the callback prompt.
old_command = '''        "📢 إرسال إعلان\\n\\n"\n        "أرسل الآن نص الإعلان الذي تريد "\n        "إرساله لجميع مستخدمي البوت.\\n\\n"\n        "❌ للإلغاء أرسل:\\n"\n        "/cancel"'''
new_command = '''        "📢 إرسال إعلان\\n\\n"\n        "أرسل نصًا أو صورة أو فيديو أو صوتًا/رسالة صوتية.\\n"\n        "يمكنك إضافة وصف مع الوسائط.\\n\\n"\n        "❌ للإلغاء أرسل /cancel"'''
if old_command not in text:
    raise RuntimeError("broadcast command prompt anchor missing")
text = text.replace(old_command, new_command, 1)

# Add a dedicated media handler before the admin text router. This does not
# alter the existing text router or ordinary-user message handling.
router_anchor = '''    app.add_handler(\n        MessageHandler(\n            filters.TEXT\n            & ~filters.COMMAND\n            & filters.User(ADMIN_ID),\n            admin_text_router\n        )\n    )\n'''
if router_anchor not in text:
    raise RuntimeError("admin text router handler anchor missing")

media_handler = '''    app.add_handler(\n        MessageHandler(\n            (filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE)\n            & filters.User(ADMIN_ID),\n            process_broadcast_media\n        )\n    )\n\n'''
text = text.replace(router_anchor, media_handler + router_anchor, 1)

text += "\n" + MARKER + "\n"
PATH.write_text(text, encoding="utf-8")
print("AliBot media broadcast fixed successfully")
