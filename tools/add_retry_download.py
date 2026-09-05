from pathlib import Path


BOT_FILE = Path("bot.py")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one anchor, found {count}"
        )
    return text.replace(old, new, 1)


def patch_save_download(text):
    old = '''    conn.commit()\n    conn.close()\n\n\n# ============================================================\n# اللغة\n# ============================================================\n'''
    new = '''    download_id = cur.lastrowid\n\n    conn.commit()\n    conn.close()\n\n    return download_id\n\n\n# ============================================================\n# اللغة\n# ============================================================\n'''
    return replace_once(
        text,
        old,
        new,
        "save_download return id",
    )


def add_retry_lookup_and_callback(text):
    anchor = '''# ============================================================\n# القوائم\n# ============================================================\n\nasync def show_main_menu(\n'''

    block = '''# ============================================================\n# إعادة التحميل بنقرة واحدة\n# ============================================================\n\ndef get_download_for_user(download_id, user_id):\n    conn = get_db()\n    try:\n        cur = conn.cursor()\n        cur.execute(\n            """\n            SELECT id, url, media_type, quality\n            FROM downloads\n            WHERE id = ? AND user_id = ?\n            LIMIT 1\n            """,\n            (download_id, user_id),\n        )\n        return cur.fetchone()\n    finally:\n        conn.close()\n\n\nasync def retry_download_callback(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE,\n):\n    query = update.callback_query\n    await query.answer()\n\n    user = update.effective_user\n    if not user:\n        return\n\n    register_user(user)\n\n    if is_banned(user.id):\n        await query.edit_message_text(\n            TEXTS["ar"]["banned"]\n        )\n        return\n\n    language = get_language(user.id) or "ar"\n\n    try:\n        download_id = int(\n            query.data.removeprefix("retry_download_")\n        )\n    except (TypeError, ValueError):\n        await query.edit_message_text(\n            "❌ تعذر استعادة هذا التحميل."\n        )\n        return\n\n    row = get_download_for_user(\n        download_id,\n        user.id,\n    )\n\n    if not row or not row["url"]:\n        await query.edit_message_text(\n            "❌ تعذر استعادة هذا الرابط. أرسل الرابط مرة أخرى."\n        )\n        return\n\n    # إعادة استخدام الرابط المحفوظ فقط لهذا المستخدم.\n    # لا يتم تمرير الرابط داخل callback_data.\n    context.user_data["video_url"] = row["url"]\n\n    keyboard = InlineKeyboardMarkup([\n        [\n            InlineKeyboardButton(\n                TEXTS[language]["video_type"],\n                callback_data="video_menu",\n            )\n        ],\n        [\n            InlineKeyboardButton(\n                TEXTS[language]["audio_type"],\n                callback_data="audio_menu",\n            )\n        ],\n    ])\n\n    await query.edit_message_text(\n        TEXTS[language]["received"],\n        reply_markup=keyboard,\n    )\n\n\n# ============================================================\n# القوائم\n# ============================================================\n\nasync def show_main_menu(\n'''

    return replace_once(
        text,
        anchor,
        block,
        "retry callback insertion",
    )


def capture_download_id_and_add_button(text):
    old = '''        save_download(\n            user=user,\n            url=url,\n            website=website,\n            media_type=(\n                "audio"\n                if is_audio\n                else "video"\n            ),\n            quality=quality_name,\n        )\n\n        # --------------------------------------------------------\n        # حذف رسالة الأزرار\n        # --------------------------------------------------------\n'''

    new = '''        download_id = save_download(\n            user=user,\n            url=url,\n            website=website,\n            media_type=(\n                "audio"\n                if is_audio\n                else "video"\n            ),\n            quality=quality_name,\n        )\n\n        # زر مستقل بعد الملف حتى لا نلمس مسار إرسال الفيديو/الصوت\n        # نفسه. الضغط عليه يعيد فتح اختيار النوع والجودة للرابط\n        # المحفوظ، مع التحقق من ملكية السجل في قاعدة البيانات.\n        retry_labels = {\n            "ar": "🔄 تحميل مرة أخرى",\n            "en": "🔄 Download again",\n            "tr": "🔄 Tekrar indir",\n            "de": "🔄 Erneut herunterladen",\n        }\n\n        try:\n            await context.bot.send_message(\n                chat_id=update.effective_chat.id,\n                text=retry_labels.get(\n                    language,\n                    retry_labels["ar"],\n                ),\n                reply_markup=InlineKeyboardMarkup([\n                    [\n                        InlineKeyboardButton(\n                            retry_labels.get(\n                                language,\n                                retry_labels["ar"],\n                            ),\n                            callback_data=f"retry_download_{download_id}",\n                        )\n                    ]\n                ]),\n            )\n        except Exception as retry_button_error:\n            # فشل زر الواجهة لا يجب أن يحول نجاح التحميل إلى فشل.\n            logger.warning(\n                "Retry button delivery failed: %s",\n                type(retry_button_error).__name__,\n            )\n\n        # --------------------------------------------------------\n        # حذف رسالة الأزرار\n        # --------------------------------------------------------\n'''

    return replace_once(
        text,
        old,
        new,
        "retry button after successful download",
    )


def register_handler(text):
    anchor = '''    # ========================================================\n    # التحميل\n    # ========================================================\n\n    app.add_handler(\n        CallbackQueryHandler(\n            download_media,\n'''

    new = '''    # ========================================================\n    # إعادة التحميل\n    # ========================================================\n\n    app.add_handler(\n        CallbackQueryHandler(\n            retry_download_callback,\n            pattern=r"^retry_download_[0-9]+$"\n        )\n    )\n\n    # ========================================================\n    # التحميل\n    # ========================================================\n\n    app.add_handler(\n        CallbackQueryHandler(\n            download_media,\n'''

    return replace_once(
        text,
        anchor,
        new,
        "retry callback handler registration",
    )


def main():
    text = BOT_FILE.read_text(encoding="utf-8")

    if "async def retry_download_callback(" in text:
        raise RuntimeError("retry download feature is already present")

    text = patch_save_download(text)
    text = add_retry_lookup_and_callback(text)
    text = capture_download_id_and_add_button(text)
    text = register_handler(text)

    BOT_FILE.write_text(text, encoding="utf-8")
    print("✅ One-click retry download feature applied.")


if __name__ == "__main__":
    main()
