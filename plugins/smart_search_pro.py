"""Smart Search Pro for AliBot.

An isolated, deterministic search layer that sits in front of the existing
Smart Search implementation. It adds bounded multi-stage querying, Arabic
normalization, fuzzy title relevance, deduplication and short-lived caching.
It never bypasses YouTube access controls and never changes the downloader.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import html
import logging
import re
import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)

from downloader.smart_search import SearchResult, search as base_search
from plugins.smart_download_control import show_control_for_url
from plugins.localization import t, language as normalize_language

URL_RE = re.compile(r"^https?://", re.IGNORECASE)
PICK_RE = re.compile(r"^smart_pro_pick_(\d+)$")
NAV_RE = re.compile(r"^smart_pro_(new|cancel)$")
PAGE_RE = re.compile(r"^smart_pro_page_(\d+)$")
MAX_QUERY_LENGTH = 160
PAGE_SIZE = 5
MAX_SEARCH_RESULTS = 25
CACHE_TTL_SECONDS = 120
MAX_CACHE_ITEMS = 128
LOW_SCORE_THRESHOLD = 42.0
SEARCH_TOTAL_TIMEOUT_SECONDS = 40
RESULT_STATE_TTL_SECONDS = CACHE_TTL_SECONDS
TELEGRAM_BUTTON_MAX_CHARS = 64
MARQUEE_INTERVAL_SECONDS = 2.2
MARQUEE_PADDING = "   •   "

_CACHE: dict[str, tuple[float, list[SearchResult]]] = {}
_CACHE_LOCK = asyncio.Lock()


def _normalize(value: str) -> str:
    value = value.casefold()
    value = value.replace("ـ", "")
    value = re.sub(r"[\u064B-\u065F\u0670]", "", value)
    value = value.translate(str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه",
        "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
        "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    }))
    value = re.sub(r"[^\w\u0600-\u06ff\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w\u0600-\u06ff]+", _normalize(value), flags=re.UNICODE)


def _parse_query_intent(query: str) -> dict[str, Any]:
    normalized = _normalize(query)
    years = re.findall(r"\b(?:19|20)\d{2}\b", normalized)
    groups = {
        "official": ("رسمي", "official"), "lyrics": ("كلمات", "lyrics", "lyric"),
        "remix": ("ريمكس", "remix"), "live": ("حفله", "حفلة", "live", "concert"),
        "short": ("short", "shorts", "قصير"),
    }
    intents = [name for name, terms in groups.items() if any(_normalize(t) in normalized for t in terms)]
    return {"years": years[:2], "intents": intents}


def _query_variants(query: str) -> list[str]:
    original = re.sub(r"\s+", " ", query).strip()[:MAX_QUERY_LENGTH]
    normalized = _normalize(original)[:MAX_QUERY_LENGTH]
    intent = _parse_query_intent(original)
    core = re.sub(r"\b(?:19|20)\d{2}\b", " ", normalized)
    core = re.sub(r"\s+", " ", core).strip()
    variants: list[str] = []
    for value in (original, normalized, core if intent["years"] else ""):
        if value and value not in variants:
            variants.append(value)
    return variants[:3]


def _youtube_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/|youtube\.com/(?:shorts|embed)/)([A-Za-z0-9_-]{6,20})", url, re.IGNORECASE)
    return match.group(1).casefold() if match else ""


def _canonical_result_key(result: SearchResult) -> str:
    video_id = _youtube_video_id(result.url)
    if video_id:
        return f"youtube:{video_id}"
    return result.url.rstrip("/").casefold()


def _title_score(query: str, result: SearchResult) -> float:
    q = _normalize(query)
    title = _normalize(result.title)
    if not q or not title:
        return 0.0
    score = 0.0
    if q == title:
        score += 45.0
    elif q in title:
        score += 34.0
    q_tokens = set(_tokens(query))
    t_tokens = set(_tokens(result.title))
    if q_tokens:
        score += (len(q_tokens & t_tokens) / len(q_tokens)) * 35.0
    score += difflib.SequenceMatcher(None, q, title).ratio() * 20.0
    return score


def _channel_score(query: str, result: SearchResult) -> float:
    q_tokens = set(_tokens(query))
    channel_tokens = set(_tokens(result.channel))
    if not q_tokens or not channel_tokens:
        return 0.0
    overlap = len(q_tokens & channel_tokens) / len(q_tokens)
    return min(overlap * 12.0, 12.0)


def _intent_score(intent: dict[str, Any], result: SearchResult) -> float:
    title = _normalize(result.title)
    groups = {
        "official": ("رسمي", "official"), "lyrics": ("كلمات", "lyrics", "lyric"),
        "remix": ("ريمكس", "remix"), "live": ("حفله", "حفلة", "live", "concert"),
        "short": ("short", "shorts", "قصير"),
    }
    return sum(8.0 for name in intent.get("intents", []) if any(_normalize(t) in title for t in groups[name]))


def _duration_sanity_score(result: SearchResult) -> float:
    if result.duration is None:
        return 0.0
    if result.duration <= 0:
        return -10.0
    if result.duration > 6 * 60 * 60:
        return -5.0
    return 0.0


def _dedupe_and_rank(query: str, candidates: list[SearchResult], limit: int = MAX_SEARCH_RESULTS) -> list[SearchResult]:
    intent = _parse_query_intent(query)
    by_identity: dict[str, SearchResult] = {}
    for result in candidates:
        key = _canonical_result_key(result)
        title = _normalize(result.title)
        if not key or not title:
            continue
        candidate_score = (
            max(result.score, 0.0)
            + _title_score(query, result)
            + _channel_score(query, result)
            + _intent_score(intent, result)
            + _duration_sanity_score(result)
        )
        current = by_identity.get(key)
        if current is None or candidate_score > current.score:
            by_identity[key] = SearchResult(
                result.index, result.title, result.url, result.channel,
                result.duration, result.views, candidate_score,
            )
    ranked = sorted(by_identity.values(), key=lambda item: (-item.score, item.title.casefold(), item.url))
    return [
        SearchResult(i, item.title, item.url, item.channel, item.duration, item.views, item.score)
        for i, item in enumerate(ranked[:limit])
    ]

def _cache_key(query: str) -> str:
    return _normalize(query)[:MAX_QUERY_LENGTH]


def _telemetry(event: str, query_hash: str, **fields: Any) -> None:
    safe = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("smart_search_telemetry event=%s query_hash=%s %s", event, query_hash, safe)


async def _cache_get(key: str, query_hash: str) -> list[SearchResult] | None:
    now = time.monotonic()
    async with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        created, results = item
        if now - created > CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
            _telemetry("cache_expired", query_hash)
            return None
        _telemetry("cache_hit", query_hash, result_count=len(results))
        return list(results)


async def _cache_put(key: str, results: list[SearchResult]) -> None:
    async with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), list(results))
        if len(_CACHE) > MAX_CACHE_ITEMS:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)


def _format_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_views(views: int | None) -> str:
    if views is None or views < 0:
        return ""
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)


def _button_meta(result: SearchResult) -> str:
    meta: list[str] = []
    if result.channel:
        channel = re.sub(r"\s+", " ", result.channel).strip()[:12]
        if channel:
            meta.append(f"📺 {channel}")
    duration = _format_duration(result.duration)
    if duration:
        meta.append(f"⏱ {duration}")
    views = _format_views(result.views)
    if views:
        meta.append(f"👁 {views}")
    return "  •  ".join(meta)


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def _title_window(title: str, width: int, offset: int = 0) -> str:
    """Return a moving window that eventually exposes every title character."""
    if width <= 0:
        return ""
    title = _clean_title(title)
    if len(title) <= width:
        return title
    stream = title + MARQUEE_PADDING + title
    max_offset = len(title) + len(MARQUEE_PADDING)
    start = offset % max_offset
    return stream[start:start + width].rstrip()


def _button_label(index: int, result: SearchResult, title_offset: int = 0) -> str:
    """Build a distinctive multi-line button while respecting Telegram's 64-char limit."""
    meta_text = _button_meta(result)
    prefix = f"{index + 1}️⃣  "
    suffix = f"\n{meta_text}" if meta_text else ""
    available = TELEGRAM_BUTTON_MAX_CHARS - len(prefix) - len(suffix)
    title = _clean_title(result.title)
    if available < 1:
        return (prefix + suffix)[:TELEGRAM_BUTTON_MAX_CHARS]
    visible_title = _title_window(title, available, title_offset)
    return f"{prefix}{visible_title}{suffix}"


def _results_message(query: str, results: list[SearchResult], page: int = 0, language: str = "ar") -> str:
    """Render only the visible page so navigation has immediate visual feedback."""
    total = len(results)
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    visible = results[start:start + PAGE_SIZE]
    end = start + len(visible)

    language = normalize_language(language)
    lines = [
        t("smart_search", "title", language),
        f"🔤 <b>{html.escape(_clean_title(query))}</b>",
        t("smart_search", "page", language, start=start + 1, end=end, total=total, page=page + 1, pages=total_pages),
        "",
    ]
    for index, result in enumerate(visible, start=start + 1):
        title = html.escape(_clean_title(result.title))
        lines.append(f"{index}. {title}")
    return "\n".join(lines)


def _results_from_state(items: list[Any]) -> list[SearchResult]:
    """Normalize persisted callback state back to the canonical SearchResult model."""
    normalized: list[SearchResult] = []
    for index, item in enumerate(items):
        if isinstance(item, SearchResult):
            normalized.append(item)
            continue
        if not isinstance(item, dict):
            continue
        normalized.append(
            SearchResult(
                index=index,
                title=str(item.get("title") or "").strip(),
                url=str(item.get("url") or "").strip(),
                channel=str(item.get("channel") or "").strip(),
                duration=item.get("duration"),
                views=item.get("views"),
                score=float(item.get("score") or 0.0),
            )
        )
    return [item for item in normalized if item.title and item.url]


def _results_keyboard(results: list[SearchResult], page: int = 0, title_offset: int = 0, language: str = "ar") -> InlineKeyboardMarkup:
    """Render a stable, bounded result page with explicit navigation."""
    language = normalize_language(language)
    total_pages = max((len(results) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    visible = results[start:start + PAGE_SIZE]

    keyboard = [
        [
            InlineKeyboardButton(
                _button_label(start + index, result, title_offset),
                callback_data=f"smart_pro_pick_{start + index}",
            )
        ]
        for index, result in enumerate(visible)
    ]

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(t("smart_search", "previous", language), callback_data=f"smart_pro_page_{page - 1}")
        )
    if start + PAGE_SIZE < len(results):
        navigation.append(
            InlineKeyboardButton(t("smart_search", "more", language), callback_data=f"smart_pro_page_{page + 1}")
        )
    if navigation:
        keyboard.append(navigation)

    keyboard.append([
        InlineKeyboardButton(t("smart_search", "new", language), callback_data="smart_pro_new"),
        InlineKeyboardButton(t("smart_search", "cancel", language), callback_data="smart_pro_cancel"),
    ])
    return InlineKeyboardMarkup(keyboard)


async def _stop_marquee(context: ContextTypes.DEFAULT_TYPE) -> None:
    task = context.user_data.pop("smart_search_marquee_task", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _animate_result_buttons(
    message: Any,
    results: list[SearchResult],
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Animate only the keyboard; selection callbacks remain unchanged."""
    if not any(len(_clean_title(result.title)) > 28 for result in results):
        return
    offset = 0
    try:
        while True:
            await asyncio.sleep(MARQUEE_INTERVAL_SECONDS)
            offset += 3
            await message.edit_reply_markup(reply_markup=_results_keyboard(results, page=0, title_offset=offset, language=normalize_language(context.user_data.get("smart_search_language"))))
    except asyncio.CancelledError:
        raise
    except Exception:
        # A stale/deleted Telegram message must never affect the download pipeline.
        return


async def search_pro(query: str) -> list[SearchResult]:
    query = query.strip()[:MAX_QUERY_LENGTH]
    if not query:
        return []
    started = time.monotonic()
    query_hash = hashlib.sha256(_normalize(query).encode("utf-8")).hexdigest()[:12]
    key = _cache_key(query)
    cached = await _cache_get(key, query_hash)
    if cached is not None:
        return cached

    variants = _query_variants(query)
    intent = _parse_query_intent(query)
    _telemetry("started", query_hash, intents=",".join(intent["intents"]) or "none", years=",".join(intent["years"]) or "none")
    candidates: list[SearchResult] = []
    try:
        async with asyncio.timeout(SEARCH_TOTAL_TIMEOUT_SECONDS):
            first = await base_search(variants[0])
            candidates.extend(first)
            first_ranked = _dedupe_and_rank(query, first, MAX_SEARCH_RESULTS)
            if len(first_ranked) < PAGE_SIZE or (first_ranked and first_ranked[0].score < LOW_SCORE_THRESHOLD):
                for variant in variants[1:]:
                    if variant == variants[0]:
                        continue
                    second = await base_search(variant)
                    candidates.extend(second)
                    if len(_dedupe_and_rank(query, candidates, MAX_SEARCH_RESULTS)) >= MAX_SEARCH_RESULTS:
                        break
    except TimeoutError:
        pass

    results = _dedupe_and_rank(query, candidates, MAX_SEARCH_RESULTS)
    await _cache_put(key, results)
    _telemetry("completed", query_hash, result_count=len(results), latency_ms=round((time.monotonic() - started) * 1000))
    return results

def _is_admin_workflow(context: ContextTypes.DEFAULT_TYPE, bot_module: Any, user_id: int) -> bool:
    if user_id != bot_module.ADMIN_ID:
        return False
    return any(
        context.user_data.get(key)
        for key in (
            "waiting_broadcast",
            "waiting_user_message",
            "waiting_admin_search",
            # Admin Group Publisher owns the next private text while the
            # administrator is composing a group broadcast. Smart Search
            # must not intercept that message.
            "admin_group_waiting_message",
        )
    )


async def _search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    if not update.message or not update.effective_user:
        return
    text = (update.message.text or "").strip()
    if not text or text.startswith("/") or URL_RE.match(text):
        return
    user = update.effective_user
    # Media Studio owns the next text message after a custom-trim prompt.
    # Keep this guard in Smart Search as a second routing boundary so a
    # pending Studio action can never fall through into search, even if
    # handler ordering changes or another text router is introduced.
    if context.user_data.get("media_studio_pending"):
        raise ApplicationHandlerStop
    if _is_admin_workflow(context, bot_module, user.id):
        return
    bot_module.register_user(user)
    if bot_module.is_banned(user.id):
        await update.message.reply_text(bot_module.TEXTS["ar"]["banned"])
        raise ApplicationHandlerStop
    language = bot_module.get_language(user.id) or "ar"
    if not bot_module.get_language(user.id):
        await update.message.reply_text(bot_module.TEXTS["ar"]["choose_language"], reply_markup=bot_module.language_keyboard())
        raise ApplicationHandlerStop
    if len(text) < 2 or len(text) > MAX_QUERY_LENGTH:
        await update.message.reply_text(t("smart_search", "invalid", language))
        raise ApplicationHandlerStop

    await _stop_marquee(context)
    current_task = asyncio.current_task()
    previous_task = context.user_data.get("smart_search_task")
    if previous_task and previous_task is not current_task and not previous_task.done():
        previous_task.cancel()
    context.user_data["smart_search_task"] = current_task
    context.user_data["smart_search_language"] = language
    query_hash = hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:12]
    logger.info("smart_search_started query_hash=%s", query_hash)
    status = await update.message.reply_text(t("smart_search", "searching", language))
    try:
        results = await search_pro(text)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("smart_search_failed query_hash=%s", query_hash)
        results = []
    if not results:
        logger.info("smart_search_completed query_hash=%s result_count=0", query_hash)
        await status.edit_text(t("smart_search", "empty", language))
        if context.user_data.get("smart_search_task") is current_task:
            context.user_data.pop("smart_search_task", None)
        raise ApplicationHandlerStop

    logger.info("smart_search_completed query_hash=%s result_count=%d", query_hash, len(results))
    context.user_data["smart_search_query"] = text
    context.user_data["smart_search_results"] = [
        {
            "url": r.url,
            "title": r.title,
            "channel": r.channel,
            "duration": r.duration,
            "views": r.views,
            "score": r.score,
        }
        for r in results
    ]
    context.user_data["smart_search_page"] = 0
    context.user_data["smart_search_results_expires_at"] = time.monotonic() + RESULT_STATE_TTL_SECONDS
    await status.edit_text(
        _results_message(text, results, page=0, language=language),
        parse_mode="HTML",
        reply_markup=_results_keyboard(results, page=0, language=language),
    )
    raise ApplicationHandlerStop


async def _pick_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if not user:
        return
    match = PICK_RE.match(query.data or "")
    if not match:
        return
    await _stop_marquee(context)
    language = normalize_language(context.user_data.get("smart_search_language"))
    expires_at = float(context.user_data.get("smart_search_results_expires_at") or 0.0)
    results = _results_from_state(context.user_data.get("smart_search_results") or [])
    index = int(match.group(1))
    if not results or time.monotonic() > expires_at or index < 0 or index >= len(results):
        context.user_data.pop("smart_search_results", None)
        context.user_data.pop("smart_search_query", None)
        context.user_data.pop("smart_search_results_expires_at", None)
        await query.edit_message_text(t("smart_search", "expired", language))
        return
    selected = results[index]
    _telemetry("result_selected", hashlib.sha256(_normalize(context.user_data.get("smart_search_query", "")).encode("utf-8")).hexdigest()[:12], index=index, page=index // PAGE_SIZE, position=(index % PAGE_SIZE) + 1, result_count=len(results))
    try:
        bot_module.validate_public_http_url(selected.url)
    except Exception:
        await query.edit_message_text(t("smart_search", "invalid_result", language))
        return

    # Smart Search hands the selected URL to the canonical Smart Download
    # Control instead of maintaining a second download-choice UX. Keep the
    # selection state until the handoff succeeds so a transient exception does
    # not strand the user with the previous generic "unexpected error" response.
    await query.edit_message_text(
        f"{t('smart_search', 'selected', language)}\n{html.escape(selected.title[:200])}\n\n{t('smart_search', 'opening', language)}",
        parse_mode="HTML",
    )
    try:
        handled = await show_control_for_url(query.message, context, selected.url, user)
    except Exception:
        logger.exception(
            "smart_search_handoff_failed index=%d url_hash=%s",
            index,
            hashlib.sha256(selected.url.encode("utf-8")).hexdigest()[:12],
        )
        await query.edit_message_text(
            t("smart_search", "retry_failed", language),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t("smart_search", "retry", language), callback_data=f"smart_pro_pick_{index}")],
                [InlineKeyboardButton(t("smart_search", "cancel", language), callback_data="smart_pro_cancel")],
            ]),
        )
        return
    if not handled:
        logger.warning(
            "smart_search_handoff_not_handled index=%d url_hash=%s",
            index,
            hashlib.sha256(selected.url.encode("utf-8")).hexdigest()[:12],
        )
        return

    context.user_data.pop("smart_search_results", None)
    context.user_data.pop("smart_search_query", None)
    context.user_data.pop("smart_search_results_expires_at", None)
    context.user_data.pop("smart_search_page", None)
    raise ApplicationHandlerStop


async def _page_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    match = PAGE_RE.match(query.data or "")
    if not match:
        return
    await _stop_marquee(context)
    page = int(match.group(1))
    results = _results_from_state(context.user_data.get("smart_search_results") or [])
    expires_at = float(context.user_data.get("smart_search_results_expires_at") or 0.0)
    max_page = max((len(results) - 1) // PAGE_SIZE, 0)
    if not results or time.monotonic() > expires_at or page < 0 or page > max_page:
        await query.edit_message_text(t("smart_search", "expired", normalize_language(context.user_data.get("smart_search_language"))))
        return
    context.user_data["smart_search_page"] = page
    await query.edit_message_text(
        _results_message(context.user_data.get("smart_search_query", ""), results, page=page, language=normalize_language(context.user_data.get("smart_search_language"))),
        parse_mode="HTML",
        reply_markup=_results_keyboard(results, page=page, language=normalize_language(context.user_data.get("smart_search_language"))),
    )
    _telemetry("page_viewed", hashlib.sha256(_normalize(context.user_data.get("smart_search_query", "")).encode("utf-8")).hexdigest()[:12], page=page, result_count=len(results))


async def _navigation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not query.data or not NAV_RE.match(query.data):
        return

    await _stop_marquee(context)
    context.user_data.pop("smart_search_results", None)
    context.user_data.pop("smart_search_query", None)
    context.user_data.pop("smart_search_results_expires_at", None)
    context.user_data.pop("smart_search_page", None)

    if query.data == "smart_pro_cancel":
        await query.edit_message_text(t("smart_search", "cancelled", normalize_language(context.user_data.get("smart_search_language"))))
        return

    await query.edit_message_text(t("smart_search", "new_prompt", normalize_language(context.user_data.get("smart_search_language"))), parse_mode="HTML")


def register_smart_search_pro(app: Any, bot_module: Any | None = None) -> None:
    bot_module = bot_module or __import__("bot")
    """Register Pro search ahead of legacy catch-all text handlers."""
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: _search_handler(u, c, bot_module)),
        group=-2,
    )
    app.add_handler(
        CallbackQueryHandler(lambda u, c: _pick_handler(u, c, bot_module), pattern=r"^smart_pro_pick_\d+$"),
        group=-2,
    )
    app.add_handler(
        CallbackQueryHandler(_page_handler, pattern=PAGE_RE.pattern),
        group=-2,
    )
    app.add_handler(
        CallbackQueryHandler(_navigation_handler, pattern=NAV_RE.pattern),
        group=-2,
    )
    print("🔎 Smart Search Pro: ENABLED", flush=True)
