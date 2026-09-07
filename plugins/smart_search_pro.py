"""Smart Search Pro for AliBot.

An isolated, deterministic search layer that sits in front of the existing
Smart Search implementation. It adds bounded multi-stage querying, Arabic
normalization, fuzzy title relevance, deduplication and short-lived caching.
It never bypasses YouTube access controls and never changes the downloader.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from downloader.smart_search import SearchResult, search as base_search

URL_RE = re.compile(r"^https?://", re.IGNORECASE)
PICK_RE = re.compile(r"^smart_pro_pick_(\d+)$")
MAX_QUERY_LENGTH = 160
CACHE_TTL_SECONDS = 120
MAX_CACHE_ITEMS = 128
LOW_SCORE_THRESHOLD = 42.0

_CACHE: dict[str, tuple[float, list[SearchResult]]] = {}
_CACHE_LOCK = asyncio.Lock()


def _normalize(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[\u064B-\u065F\u0670]", "", value)
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))
    value = re.sub(r"[^\w\u0600-\u06ff\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w\u0600-\u06ff]+", _normalize(value), flags=re.UNICODE)


def _query_variants(query: str) -> list[str]:
    original = re.sub(r"\s+", " ", query).strip()[:MAX_QUERY_LENGTH]
    normalized = _normalize(original)[:MAX_QUERY_LENGTH]
    variants: list[str] = []
    for value in (original, normalized):
        if value and value not in variants:
            variants.append(value)
    return variants[:2]


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


def _dedupe_and_rank(query: str, candidates: list[SearchResult]) -> list[SearchResult]:
    by_url: dict[str, SearchResult] = {}
    by_title: dict[str, SearchResult] = {}
    for result in candidates:
        key_url = result.url.rstrip("/").casefold()
        key_title = _normalize(result.title)
        if not key_url or not key_title:
            continue
        candidate_score = max(result.score, 0.0) + _title_score(query, result)
        current = by_url.get(key_url)
        if current is None or candidate_score > current.score:
            by_url[key_url] = SearchResult(result.index, result.title, result.url, result.channel, result.duration, result.views, candidate_score)
    for result in by_url.values():
        key_title = _normalize(result.title)
        current = by_title.get(key_title)
        if current is None or result.score > current.score:
            by_title[key_title] = result
    ranked = sorted(by_title.values(), key=lambda item: (-item.score, item.title.casefold(), item.url))
    return [
        SearchResult(i, item.title, item.url, item.channel, item.duration, item.views, item.score)
        for i, item in enumerate(ranked[:5])
    ]


def _cache_key(query: str) -> str:
    return _normalize(query)[:MAX_QUERY_LENGTH]


async def _cache_get(key: str) -> list[SearchResult] | None:
    now = time.monotonic()
    async with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        created, results = item
        if now - created > CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
            return None
        return list(results)


async def _cache_put(key: str, results: list[SearchResult]) -> None:
    async with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), list(results))
        if len(_CACHE) > MAX_CACHE_ITEMS:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)


async def search_pro(query: str) -> list[SearchResult]:
    query = query.strip()[:MAX_QUERY_LENGTH]
    if not query:
        return []
    key = _cache_key(query)
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    variants = _query_variants(query)
    candidates: list[SearchResult] = []
    first = await base_search(variants[0])
    candidates.extend(first)

    # A second bounded search is used only when the first pass is weak.
    first_ranked = _dedupe_and_rank(query, first)
    if len(first_ranked) < 5 or (first_ranked and first_ranked[0].score < LOW_SCORE_THRESHOLD):
        if len(variants) > 1 and variants[1] != variants[0]:
            second = await base_search(variants[1])
            candidates.extend(second)

    results = _dedupe_and_rank(query, candidates)
    await _cache_put(key, results)
    return results


def _is_admin_workflow(context: ContextTypes.DEFAULT_TYPE, bot_module: Any, user_id: int) -> bool:
    if user_id != bot_module.ADMIN_ID:
        return False
    return any(context.user_data.get(key) for key in ("waiting_broadcast", "waiting_user_message", "waiting_admin_search"))


async def _search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_module: Any) -> None:
    if not update.message or not update.effective_user:
        return
    text = (update.message.text or "").strip()
    if not text or text.startswith("/") or URL_RE.match(text):
        return
    user = update.effective_user
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
        await update.message.reply_text("❌ اكتب عبارة بحث بين حرفين و160 حرفًا.")
        raise ApplicationHandlerStop

    status = await update.message.reply_text("🔎 جاري البحث الذكي الاحترافي...\n\n⚙️ يتم تحليل وترتيب النتائج خوارزميًا.")
    results = await search_pro(text)
    if not results:
        await status.edit_text("❌ لم أجد نتائج مناسبة. جرّب كلمات بحث مختلفة.")
        raise ApplicationHandlerStop

    context.user_data["smart_search_results"] = [{"url": r.url, "title": r.title} for r in results]
    lines = ["🔎 <b>نتائج البحث الذكي الاحترافي</b>", "━━━━━━━━━━━━━━━━━━", ""]
    keyboard = []
    for index, result in enumerate(results):
        meta = []
        if result.channel:
            meta.append(result.channel[:36])
        if result.duration is not None:
            minutes, seconds = divmod(max(result.duration, 0), 60)
            meta.append(f"{minutes}:{seconds:02d}")
        if result.views is not None and result.views >= 1000:
            meta.append(f"{result.views / 1_000_000:.1f}M" if result.views >= 1_000_000 else f"{result.views / 1_000:.1f}K")
        suffix = f" — {' • '.join(meta)}" if meta else ""
        lines.append(f"{index + 1}. {result.title[:80]}{suffix}")
        keyboard.append([InlineKeyboardButton(f"{index + 1}️⃣ {result.title[:45]}", callback_data=f"smart_pro_pick_{index}")])
    lines.append("\n👇 اختر النتيجة التي تريد تحميلها.")
    await status.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
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
    results = context.user_data.get("smart_search_results") or []
    index = int(match.group(1))
    if index < 0 or index >= len(results):
        await query.edit_message_text("❌ انتهت صلاحية نتائج البحث. أعد البحث من جديد.")
        return
    selected = results[index]
    try:
        bot_module.validate_public_http_url(selected["url"])
    except Exception:
        await query.edit_message_text("❌ تعذر التحقق من نتيجة البحث.")
        return
    context.user_data["video_url"] = selected["url"]
    language = bot_module.get_language(user.id) or "ar"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(bot_module.TEXTS[language]["video_type"], callback_data="video_menu")],
        [InlineKeyboardButton(bot_module.TEXTS[language]["audio_type"], callback_data="audio_menu")],
        [InlineKeyboardButton(bot_module.TEXTS[language]["back"], callback_data="main_menu")],
    ])
    await query.edit_message_text(f"🎯 <b>تم اختيار:</b>\n{selected['title'][:200]}\n\nاختر نوع التحميل:", parse_mode="HTML", reply_markup=keyboard)


def register_smart_search_pro(app: Any, bot_module: Any) -> None:
    """Register Pro search ahead of legacy catch-all text handlers."""
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: _search_handler(u, c, bot_module)),
        group=-2,
    )
    app.add_handler(
        CallbackQueryHandler(lambda u, c: _pick_handler(u, c, bot_module), pattern=r"^smart_pro_pick_\d+$"),
        group=-2,
    )
    print("🔎 Smart Search Pro: ENABLED", flush=True)
