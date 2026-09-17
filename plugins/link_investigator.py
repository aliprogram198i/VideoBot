"""Bounded AI link investigation for Smart Download Control."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
AI_TIMEOUT = 12
_INSTALLED = False
_MEDIA = {"video", "audio", "image", "mixed", "unknown"}
_PAGE = {"direct_media", "media_page", "share_redirect", "login_required", "unknown"}
_ACCESS = {"public", "login_required", "unknown"}
_STRATEGY = {"existing_smart_engine", "yt_dlp", "yoinku_fallback", "direct_fallback", "manual_review"}


def _clean(value: Any, limit: int = 240):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value[:limit] or None


def _fallback(source=None, error=None, title=None):
    return {
        "enabled": False,
        "source": _clean(source, 80) or "Other",
        "content_type": "unknown",
        "page_type": "unknown",
        "title": _clean(title),
        "is_direct": False,
        "access_status": "unknown",
        "available_qualities": [],
        "problems": [_clean(error)] if error else [],
        "recommended_strategy": "existing_smart_engine",
        "confidence": 0.0,
    }


def _normalize(raw: dict, source, title):
    media = raw.get("content_type") if raw.get("content_type") in _MEDIA else "unknown"
    page = raw.get("page_type") if raw.get("page_type") in _PAGE else "unknown"
    access = raw.get("access_status") if raw.get("access_status") in _ACCESS else "unknown"
    strategy = raw.get("recommended_strategy") if raw.get("recommended_strategy") in _STRATEGY else "existing_smart_engine"
    qualities = raw.get("available_qualities") if isinstance(raw.get("available_qualities"), list) else []
    problems = raw.get("problems") if isinstance(raw.get("problems"), list) else []
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "enabled": True,
        "source": _clean(source, 80) or "Other",
        "content_type": media,
        "page_type": page,
        "title": _clean(raw.get("title"), 240) or _clean(title, 240),
        "is_direct": bool(raw.get("is_direct")),
        "access_status": access,
        "available_qualities": [_clean(x, 40) for x in qualities if _clean(x, 40)][:12],
        "problems": [_clean(x, 180) for x in problems if _clean(x, 180)][:8],
        "recommended_strategy": strategy,
        "confidence": round(confidence, 2),
    }


def _prompt(url: str, probe: dict) -> str:
    data = {
        "url_host": (urlparse(url).hostname or "").lower(),
        "source": probe.get("source"),
        "title": probe.get("title"),
        "duration": probe.get("duration"),
        "uploader": probe.get("uploader"),
        "view_count": probe.get("view_count"),
        "width": probe.get("width"),
        "height": probe.get("height"),
        "formats": probe.get("formats", []),
        "redirected": bool(probe.get("redirected")),
        "probe_error": probe.get("probe_error"),
    }
    return (
        "Classify this public media URL for a Telegram downloader. Return ONLY valid JSON with fields "
        "content_type,page_type,title,is_direct,access_status,available_qualities,problems,recommended_strategy,confidence. "
        "content_type=video|audio|image|mixed|unknown; page_type=direct_media|media_page|share_redirect|login_required|unknown; "
        "access_status=public|login_required|unknown; recommended_strategy=existing_smart_engine|yt_dlp|yoinku_fallback|direct_fallback|manual_review. "
        "Do not invent qualities or facts. Never return commands, credentials, code, or a replacement URL. DATA="
        + json.dumps(data, ensure_ascii=False)[:14000]
    )


async def investigate(url: str, probe: dict) -> dict:
    try:
        import bot
        if getattr(bot, "gemini_client", None) is None:
            return _fallback(probe.get("source"), "AI not configured", probe.get("title"))
        text = await asyncio.wait_for(bot.gemini_generate(_prompt(url, probe)), timeout=AI_TIMEOUT)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("AI response is not an object")
        return _normalize(raw, probe.get("source"), probe.get("title"))
    except Exception as exc:
        logger.info("AI link investigation unavailable: %s", type(exc).__name__)
        return _fallback(probe.get("source"), type(exc).__name__, probe.get("title"))


def _format(info: dict, language: str) -> str:
    if not info.get("enabled"):
        return ""
    labels = {
        "ar": ("🕵️ <b>تحليل الرابط الذكي</b>", "الموقع", "نوع المحتوى", "العنوان", "نوع الوسائط", "نوع الصفحة", "تسجيل الدخول", "مباشر", "الجودة المتاحة", "المسار المقترح", "المشاكل المحتملة", "نعم", "لا"),
        "en": ("🕵️ <b>AI Link Investigation</b>", "Site", "Content type", "Title", "Media type", "Page type", "Login", "Direct", "Available quality", "Recommended path", "Potential issues", "Yes", "No"),
        "tr": ("🕵️ <b>AI bağlantı analizi</b>", "Site", "İçerik türü", "Başlık", "Medya türü", "Sayfa türü", "Giriş", "Doğrudan", "Mevcut kalite", "Önerilen yol", "Olası sorunlar", "Evet", "Hayır"),
        "de": ("🕵️ <b>KI-Linkanalyse</b>", "Website", "Inhaltstyp", "Titel", "Medientyp", "Seitentyp", "Anmeldung", "Direkt", "Verfügbare Qualität", "Empfohlener Pfad", "Mögliche Probleme", "Ja", "Nein"),
    }.get(language)
    if not labels:
        labels = ("🕵️ <b>تحليل الرابط الذكي</b>", "الموقع", "نوع المحتوى", "العنوان", "نوع الوسائط", "نوع الصفحة", "تسجيل الدخول", "مباشر", "الجودة المتاحة", "المسار المقترح", "المشاكل المحتملة", "نعم", "لا")
    quality = ", ".join(info.get("available_qualities") or []) or "—"
    issues = ", ".join(info.get("problems") or []) or "—"
    title = info.get("title") or "—"
    direct = labels[11] if info.get("is_direct") else labels[12]
    login = labels[11] if info.get("access_status") == "login_required" else labels[12] if info.get("access_status") == "public" else "غير معروف" if language == "ar" else "Unknown"
    return (
        f"\n{labels[0]}\n"
        f"🌐 {labels[1]}: {html.escape(str(info.get('source') or 'Other'))}\n"
        f"🧩 {labels[2]}: {html.escape(str(info.get('content_type') or 'unknown'))}\n"
        f"🎬 {labels[3]}: {html.escape(str(title))}\n"
        f"🎞 {labels[4]}: {html.escape(str(info.get('content_type') or 'unknown'))}\n"
        f"📄 {labels[5]}: {html.escape(str(info.get('page_type') or 'unknown'))}\n"
        f"🔐 {labels[6]}: {login}\n"
        f"🔗 {labels[7]}: {direct}\n"
        f"📐 {labels[8]}: {html.escape(quality)}\n"
        f"🧭 {labels[9]}: <code>{html.escape(str(info.get('recommended_strategy') or 'existing_smart_engine'))}</code>\n"
        f"⚠️ {labels[10]}: {html.escape(issues)}\n"
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        from . import smart_download_control as sdc
        original_probe, original_text = sdc._probe, sdc._text

        async def run_background_investigation(url: str, data: dict) -> None:
            try:
                data["ai_investigation"] = await investigate(url, data)
            except Exception as exc:
                logger.info(
                    "Background AI link investigation unavailable: %s",
                    type(exc).__name__,
                )

        async def probe_with_ai(url: str) -> dict:
            probe_error = None
            try:
                data = await original_probe(url)
            except Exception as exc:
                probe_error = type(exc).__name__
                data = {
                    "source": sdc._source(url),
                    "title": None,
                    "duration": None,
                    "uploader": None,
                    "thumbnail": None,
                    "view_count": None,
                    "width": None,
                    "height": None,
                    "formats": [],
                    "probe_error": probe_error,
                }

            # AI is auxiliary analysis only. Never block link intake or
            # the download-control handoff on Gemini availability/latency.
            data["ai_investigation"] = _fallback(
                data.get("source"),
                "background_pending",
                data.get("title"),
            )
            asyncio.create_task(
                run_background_investigation(url, data)
            )
            return data

        def text_with_ai(data: dict, language: str = "ar") -> str:
            return original_text(data, language) + _format(data.get("ai_investigation") or {}, language)

        sdc._probe, sdc._text = probe_with_ai, text_with_ai
        _INSTALLED = True
        logger.info("AI Link Investigator: ENABLED")
    except Exception as exc:
        logger.warning("AI Link Investigator integration unavailable: %s", type(exc).__name__)


__all__ = ["investigate", "install"]
