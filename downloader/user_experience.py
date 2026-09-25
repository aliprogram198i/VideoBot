from __future__ import annotations

import asyncio
from pathlib import Path
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


RETRY_CALLBACK = "retry_download"
SAFE_TELEGRAM_VIDEO_BYTES = 47 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 240


def retry_keyboard(language: str) -> InlineKeyboardMarkup:
    labels = {
        "ar": "🔄 إعادة المحاولة",
        "en": "🔄 Retry",
        "tr": "🔄 Tekrar dene",
        "de": "🔄 Erneut versuchen",
    }
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(labels.get(language, labels["ar"]), callback_data=RETRY_CALLBACK)]]
    )


def progress_text(language: str, stage: str, platform: str, quality: str) -> str:
    stages = {
        "ar": {
            "resolve": "🔎 1/4 تحليل الرابط وتحديد المصدر",
            "download": "📥 2/4 تنزيل الوسائط",
            "fallback": "🧠 3/4 تحسين المسار وإيجاد أفضل مصدر متاح",
            "delivery": "📤 4/4 تجهيز الملف للإرسال",
        },
        "en": {
            "resolve": "🔎 1/4 Analyzing the link and source",
            "download": "📥 2/4 Downloading media",
            "fallback": "🧠 3/4 Optimizing the recovery path",
            "delivery": "📤 4/4 Preparing the file for delivery",
        },
        "tr": {
            "resolve": "🔎 1/4 Bağlantı ve kaynak analiz ediliyor",
            "download": "📥 2/4 Medya indiriliyor",
            "fallback": "🧠 3/4 En uygun kurtarma yolu deneniyor",
            "delivery": "📤 4/4 Dosya gönderime hazırlanıyor",
        },
        "de": {
            "resolve": "🔎 1/4 Link und Quelle werden analysiert",
            "download": "📥 2/4 Medien werden heruntergeladen",
            "fallback": "🧠 3/4 Wiederherstellungspfad wird optimiert",
            "delivery": "📤 4/4 Datei wird für den Versand vorbereitet",
        },
    }
    stage_text = stages.get(language, stages["ar"]).get(stage, stage)
    return (
        f"⏳ <b>AliBot</b>\n\n"
        f"🌐 {platform}\n"
        f"🎚 {quality}\n\n"
        f"{stage_text}\n"
        f"💙 يرجى الانتظار..."
        if language == "ar"
        else f"⏳ <b>AliBot</b>\n\n🌐 {platform}\n🎚 {quality}\n\n{stage_text}"
    )


def link_preview_text(language: str, platform: str, url: str) -> str:
    labels = {
        "ar": ("🔎 معاينة الرابط", "🌐 المنصة", "🔗 الرابط", "👇 اختر نوع التحميل:"),
        "en": ("🔎 Link preview", "🌐 Platform", "🔗 Link", "👇 Choose the download type:"),
        "tr": ("🔎 Bağlantı önizlemesi", "🌐 Platform", "🔗 Bağlantı", "👇 İndirme türünü seçin:"),
        "de": ("🔎 Link-Vorschau", "🌐 Plattform", "🔗 Link", "👇 Downloadtyp auswählen:"),
    }
    title, platform_label, url_label, action = labels.get(language, labels["ar"])
    display_url = url if len(url) <= 80 else f"{url[:77]}..."
    display_url = escape(display_url, quote=True)
    return f"{title}\n\n{platform_label}: <b>{platform}</b>\n{url_label}: <code>{display_url}</code>\n\n{action}"


async def optimize_video_for_telegram(
    source_path: str,
    temp_dir: str,
    *,
    max_bytes: int = SAFE_TELEGRAM_VIDEO_BYTES,
    timeout_seconds: int = FFMPEG_TIMEOUT_SECONDS,
) -> tuple[str | None, dict]:
    source = Path(source_path)
    if not source.is_file():
        return None, {"status": "failed", "reason": "source_missing"}

    source_size = source.stat().st_size
    if source_size <= max_bytes:
        return str(source), {"status": "not_needed", "source_bytes": source_size}

    output = Path(temp_dir) / f"delivery_optimized_{source.stem}.mp4"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-vf", "scale=min(720\\,iw):-2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "29",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        str(output),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return None, {
                "status": "failed",
                "reason": "timeout",
                "source_bytes": source_size,
            }

        if process.returncode != 0 or not output.is_file():
            return None, {
                "status": "failed",
                "reason": "ffmpeg_failed",
                "source_bytes": source_size,
                "error": stderr.decode("utf-8", errors="replace")[-500:],
            }

        optimized_size = output.stat().st_size
        if optimized_size <= max_bytes:
            return str(output), {
                "status": "optimized",
                "source_bytes": source_size,
                "optimized_bytes": optimized_size,
            }

        return None, {
            "status": "still_too_large",
            "source_bytes": source_size,
            "optimized_bytes": optimized_size,
        }
    except (OSError, ValueError) as exc:
        return None, {
            "status": "failed",
            "reason": type(exc).__name__,
            "source_bytes": source_size,
        }
