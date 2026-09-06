"""Deterministic Smart Search pipeline for AliBot.

Pipeline:
Normalize -> Expand -> Fetch -> Deduplicate -> Relevance Score
-> Quality Filter -> Rank -> Top 5.

This module is intentionally independent from the downloader, Facebook
runtime, and Telegram handler registration. It only returns validated
YouTube result metadata for the existing UI to consume.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import unicodedata
from typing import Any

MAX_RESULTS = 5
SEARCH_RESULTS_PER_QUERY = 10
MAX_QUERY_VARIANTS = 3
SEARCH_TIMEOUT_SECONDS = 45

_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SHORTS_RE = re.compile(r"\bshorts?\b", re.IGNORECASE)
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "من", "في", "على", "عن", "و", "وهو", "هي", "هذا", "هذه", "الى", "إلى", "يا",
}


def normalize_text(text: str) -> str:
    """Normalize Arabic/Latin text without changing its semantic tokens."""
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    value = _ARABIC_DIACRITICS_RE.sub("", value).replace("ـ", "")
    value = (
        value.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
    )
    value = _PUNCT_RE.sub(" ", value)
    return " ".join(value.split())


def tokenize(text: str) -> list[str]:
    return [token for token in normalize_text(text).split() if token]


def expand_query(query: str) -> list[str]:
    """Generate a small deterministic set of safe search variants."""
    original = " ".join(str(query or "").split())
    normalized = normalize_text(original)
    variants: list[str] = []

    for value in (original, normalized):
        if value and value not in variants:
            variants.append(value)

    tokens = tokenize(original)
    if len(tokens) >= 3:
        focused = " ".join(token for token in tokens if token not in _STOPWORDS)
        if focused and focused not in variants:
            variants.append(focused)

    return variants[:MAX_QUERY_VARIANTS]


def _build_command(query: str) -> list[str]:
    command = [
        "python", "-m", "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--playlist-end", str(SEARCH_RESULTS_PER_QUERY),
        f"ytsearch{SEARCH_RESULTS_PER_QUERY}:{query}",
    ]
    if shutil.which("deno"):
        command[4:4] = ["--js-runtimes", "deno"]
    return command


async def _fetch_variant(query: str) -> list[dict[str, Any]]:
    process = await asyncio.create_subprocess_exec(
        *_build_command(query),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("search_timeout")

    if process.returncode != 0:
        error = (stderr or b"").decode("utf-8", "ignore")[-500:]
        raise RuntimeError(error or "search_failed")

    try:
        payload = json.loads(stdout.decode("utf-8", "ignore"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("search_invalid_response") from exc

    entries = payload.get("entries") if isinstance(payload, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _normalize_url(url: str) -> str:
    value = str(url or "").strip()
    value = value.split("&list=", 1)[0]
    value = value.split("&index=", 1)[0]
    return value


def _to_item(entry: dict[str, Any]) -> dict[str, Any] | None:
    video_id = str(entry.get("id") or "").strip()
    url = entry.get("webpage_url") or entry.get("url")
    if not url and video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"

    title = str(entry.get("title") or "").strip()
    if not url or not title:
        return None

    return {
        "url": str(url),
        "title": title,
        "channel": str(entry.get("channel") or entry.get("uploader") or "").strip(),
        "duration": entry.get("duration"),
        "view_count": entry.get("view_count"),
        "upload_date": entry.get("upload_date"),
        "is_live": bool(entry.get("is_live")),
        "id": video_id,
    }


def deduplicate(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in results:
        key = str(item.get("id") or _normalize_url(item.get("url", "")))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _query_intent(query: str) -> dict[str, bool]:
    tokens = set(tokenize(query))
    return {
        "shorts": bool(tokens & {"short", "shorts", "شورت", "شورتس"}),
        "official": bool(tokens & {"official", "officialvideo", "رسمي", "الرسمية"}),
        "full": bool(tokens & {"full", "كامل", "كاملة", "كاملها"}),
    }


def _popularity_score(value: Any) -> float:
    try:
        views = max(0, int(value or 0))
    except (TypeError, ValueError):
        views = 0
    denominator = math.log1p(100_000_000)
    return min(1.0, math.log1p(views) / denominator) if views else 0.0


def relevance_score(query: str, item: dict[str, Any]) -> float:
    query_tokens = set(tokenize(query))
    title_tokens = tokenize(item.get("title", ""))
    channel_tokens = tokenize(item.get("channel", ""))
    if not query_tokens or not title_tokens:
        return 0.0

    title_set = set(title_tokens)
    channel_set = set(channel_tokens)
    title_overlap = len(query_tokens & title_set) / len(query_tokens)
    channel_overlap = len(query_tokens & channel_set) / len(query_tokens)
    phrase = 1.0 if normalize_text(query) in normalize_text(item.get("title", "")) else 0.0

    score = (
        title_overlap * 0.58
        + phrase * 0.20
        + channel_overlap * 0.07
        + _popularity_score(item.get("view_count")) * 0.10
    )

    intent = _query_intent(query)
    duration = item.get("duration")
    if isinstance(duration, (int, float)) and 1 <= duration <= 3600:
        score += 0.05
    if intent["full"] and isinstance(duration, (int, float)) and duration >= 120:
        score += 0.10
    if intent["official"] and channel_set & {"official", "records", "music"}:
        score += 0.08

    return score


def quality_filter(query: str, item: dict[str, Any]) -> bool:
    """Remove structurally weak results without inspecting/downloading media."""
    title = normalize_text(item.get("title", ""))
    if not item.get("url") or not title:
        return False
    if item.get("is_live"):
        return False

    intent = _query_intent(query)
    if not intent["shorts"] and _SHORTS_RE.search(title):
        return False

    duration = item.get("duration")
    if isinstance(duration, (int, float)) and duration <= 0:
        return False

    return True


def rank_results(query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        item for item in deduplicate(results)
        if quality_filter(query, item)
    ]
    for item in filtered:
        item["score"] = relevance_score(query, item)

    filtered.sort(
        key=lambda item: (
            -float(item.get("score", 0.0)),
            -int(item.get("view_count") or 0),
            str(item.get("title", "")).lower(),
        )
    )
    return filtered[:MAX_RESULTS]


async def search_youtube(query: str) -> list[dict[str, Any]]:
    """Execute the complete deterministic search pipeline."""
    variants = expand_query(query)
    batches = await asyncio.gather(
        *(_fetch_variant(variant) for variant in variants),
        return_exceptions=True,
    )

    raw: list[dict[str, Any]] = []
    errors: list[Exception] = []
    for batch in batches:
        if isinstance(batch, Exception):
            errors.append(batch)
            continue
        for entry in batch:
            item = _to_item(entry)
            if item:
                raw.append(item)

    ranked = rank_results(query, raw)
    if not ranked and errors and not raw:
        raise errors[0]
    return ranked
