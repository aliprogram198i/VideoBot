"""AI-assisted link investigation built on deterministic probe data.

The investigator never downloads media and never executes AI-provided commands.
It returns a small, validated decision object that the existing downloader can
optionally use as diagnostic/strategy metadata.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

AI_TIMEOUT = 12
MAX_PROMPT_CHARS = 12000
_ALLOWED_MEDIA = {"video", "audio", "image", "mixed", "unknown"}
_ALLOWED_PAGE = {"direct_media", "media_page", "share_redirect", "login_required", "unknown"}
_ALLOWED_ACCESS = {"public", "login_required", "unknown"}
_ALLOWED_STRATEGIES = {"existing_smart_engine", "yt_dlp", "yoinku_fallback", "direct_fallback", "manual_review"}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _clean_text(value: Any, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit] if text else None


def _fallback(source: str | None = None, error: str | None = None) -> dict[str, Any]:
    return {
        "enabled": False,
        "source": source or "Other",
        "content_type": "unknown",
        "page_type": "unknown",
        "title": None,
        "is_direct": False,
        "access_status": "unknown",
        "available_qualities": [],
        "problems": [_clean_text(error)] if error else [],
        "recommended_strategy": "existing_smart_engine",
        "confidence": 0.0,
    }


def _normalize(raw: dict[str, Any], source: str | None, title: str | None) -> dict[str, Any]:
    media = raw.get("content_type") if raw.get("content_type") in _ALLOWED_MEDIA else "unknown"
    page = raw.get("page_type") if raw.get("page_type") in _ALLOWED_PAGE else "unknown"
    access = raw.get("access_status") if raw.get("access_status") in _ALLOWED_ACCESS else "unknown"
    strategy = raw.get("recommended_strategy") if raw.get("recommended_strategy") in _ALLOWED_STRATEGIES else "existing_smart_engine"
    qualities = raw.get("available_qualities")
    if not isinstance(qualities, list):
        qualities = []
    qualities = [_clean_text(item, 40) for item in qualities if _clean_text(item, 40)][:12]
    problems = raw.get("problems")
    if not isinstance(problems, list):
        problems = []
    problems = [_clean_text(item, 180) for item in problems if _clean_text(item, 180)][:8]
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "enabled": True,
        "source": _clean_text(source, 80) or "Other",
        "content_type": media,
        "page_type": page,
        "title": _clean_text(raw.get("title"), 240) or _clean_text(title, 240),
        "is_direct": bool(raw.get("is_direct")),
        "access_status": access,
        "available_qualities": qualities,
        "problems": problems,
        "recommended_strategy": strategy,
        "confidence": round(confidence, 2),
    }


def _prompt(url: str, probe: dict[str, Any]) -> str:
    safe = {
        "host": _host(url),
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
        "Classify this public media URL for a Telegram downloader. "
        "Return ONLY valid JSON, with exactly these useful fields: "
        "content_type, page_type, title, is_direct, access_status, "
        "available_qualities, problems, recommended_strategy, confidence. "
        "Allowed content_type: video,audio,image,mixed,unknown. "
        "Allowed page_type: direct_media,media_page,share_redirect,login_required,unknown. "
        "Allowed access_status: public,login_required,unknown. "
        "Allowed recommended_strategy: existing_smart_engine,yt_dlp,yoinku_fallback,direct_fallback,manual_review. "
        "Do not invent unavailable qualities. Never provide commands, URLs, credentials, or code. "
        f"DATA={json.dumps(safe, ensure_ascii=False)[:MAX_PROMPT_CHARS]}"
    )


async def investigate(url: str, probe: dict[str, Any]) -> dict[str, Any]:
    """Run bounded AI classification; return deterministic fallback on failure."""
    try:
        import bot
        if getattr(bot, "gemini_client", None) is None:
            return _fallback(probe.get("source"), "AI not configured")
        raw_text = await asyncio.wait_for(
            bot.gemini_generate(_prompt(url, probe)), timeout=AI_TIMEOUT
        )
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.I | re.S).strip()
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            raise ValueError("AI response is not an object")
        return _normalize(raw, probe.get("source"), probe.get("title"))
    except Exception as exc:
        logger.info("AI link investigation unavailable: %s", type(exc).__name__)
        return _fallback(probe.get("source"), type(exc).__name__)


__all__ = ["investigate"]
