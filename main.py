import os
import asyncio
import yt_dlp

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "8467987610:AAHGKzP2sMlr7reGkTLMgUbpvztKz6AVFYM"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 أهلاً بك في بوت تحميل الفيديوهات!\n\n"
        "🔗 أرسل رابط الفيديو وسأقوم بتحميله لك."
    )


async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text(
            "❌ أرسل رابط فيديو صحيح يبدأ بـ http أو https."
        )
        return

    message = await update.message.reply_text(
        "🔗 تم استلام الرابط.\n"
        "⏳ جاري تحميل الفيديو..."
    )

    filename = None

    try:
        output_template = "downloads/%(title)s.%(ext)s"

        os.makedirs("downloads", exist_ok=True)

        ydl_opts = {
            "format": "best[ext=mp4]/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
        }

        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        filename = await asyncio.to_thread(download)

        if not os.path.exists(filename):
            await message.edit_text("❌ لم يتم العثور على الملف بعد التحميل.")
            return

        await message.edit_text("📤 تم التحميل بنجاح!\n⏳ جاري إرسال الفيديو...")

        with open(filename, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption="🎬 تم تحميل الفيديو بنجاح ✅"
            )

        os.remove(filename)

        await message.delete()

    except Exception as e:
        print("ERROR:", e)

        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass

        await message.edit_text(
            "❌ حدث خطأ أثناء تحميل الفيديو.\n\n"
            "تأكد من أن الرابط صحيح وحاول مرة أخرى."
        )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, download_video)
    )

    print("🤖 البوت يعمل الآن...")
    app.run_polling()


if __name__ == "__main__":
    main()
