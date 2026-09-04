"""Deterministic Smart Search for AliBot.

The search engine is intentionally non-AI. It uses yt-dlp's public YouTube
search extractor, then normalizes, deduplicates and ranks results locally.
No cookies, login sessions, DRM bypasses or access-control workarounds are
used.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


SEARCH_COUNT = 10
RESULT_COUNT = 5
TIMEOUT_SECONDS = 25
MAX_QUERY_LENGTH = 160
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class SearchResult:
    index: int
    title: str
    url: str
    channel: str
    duration: int | None
    views: int | None
    score: float


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(value: str) -> list[str]:
    value = value.casefold()
    return re.findall(r"[\w\u0600-\u06ff]+", value, flags=re.UNICODE)


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _entry_url(entry: dict[str, Any]) -> str:
    for key in ("webpage_url", "webpage_url_basename"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value

    value = entry.get("url")
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value

    video_id = entry.get("id")
    if isinstance(video_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        return f"https://www.youtube.com/watch?v={video_id}"

    return ""


def _iter_entries(payload: Any, depth: int = 0):
    if depth > 4:
        return
    if isinstance(payload, dict):
        entries = payload.get("entries")
        if isinstance(entries, list):
            for item in entries:
                yield from _iter_entries(item, depth + 1)
        elif payload.get("id") or payload.get("url"):
            yield payload


def _rank(query: str, entry: dict[str, Any]) -> float:
    title = _clean_text(entry.get("title"))
    channel = _clean_text(entry.get("channel") or entry.get("uploader"))
    q_tokens = _tokens(query)
    title_tokens = _tokens(title)
    channel_tokens = _tokens(channel)

    if not title:
        return -1000.0

    q_norm = " ".join(q_tokens)
    title_norm = " ".join(title_tokens)
    score = 0.0

    if q_norm and q_norm in title_norm:
        score += 55.0
    if q_norm and title_norm.startswith(q_norm):
        score += 18.0

    if q_tokens:
        title_set = set(title_tokens)
        overlap = sum(1 for token in q_tokens if token in title_set)
        score += (overlap / len(q_tokens)) * 45.0

        channel_overlap = sum(1 for token in q_tokens if token in set(channel_tokens))
        score += min(channel_overlap * 4.0, 12.0)

    views = _as_int(entry.get("view_count"))
    if views:
        # Logarithmic popularity bonus; never dominates textual relevance.
        import math
        score += min(math.log10(max(views, 1)) * 2.0, 20.0)

    duration = _as_int(entry.get("duration"))
    if duration is not None and duration <= 0:
        score -= 10.0

    return score


def _build_command(query: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        "ytsearch10:" + query,
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--no-playlist",
        "--socket-timeout",
        "15",
    ]


def search_sync(query: str) -> list[SearchResult]:
    query = _clean_text(query)[:MAX_QUERY_LENGTH]
    if not query:
        return []

    process = subprocess.run(
        _build_command(query),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )

    stdout = process.stdout[:MAX_OUTPUT_BYTES]
    if process.returncode != 0 or not stdout:
        return []

    try:
        payload = json.loads(stdout.decode("utf-8", errors="ignore"))
    except (TypeError, ValueError):
        return []

    results: list[SearchResult] = []
    seen: set[str] = set()

    for entry in _iter_entries(payload):
        if not isinstance(entry, dict):
            continue

        url = _entry_url(entry)
        title = _clean_text(entry.get("title"))
        if not url or not title:
            continue

        if url in seen:
            continue
        seen.add(url)

        # Reject obvious non-video search entries.
        if entry.get("live_status") in {"is_live", "post_live", "is_upcoming"}:
            continue

        results.append(
            SearchResult(
                index=0,
                title=title[:180],
                url=url,
                channel=_clean_text(entry.get("channel") or entry.get("uploader"))[:100],
                duration=_as_int(entry.get("duration")),
                views=_as_int(entry.get("view_count")),
                score=_rank(query, entry),
            )
        )

    results.sort(key=lambda item: (-item.score, item.title.casefold(), item.url))

    return [
        SearchResult(
            index=index,
            title=item.title,
            url=item.url,
            channel=item.channel,
            duration=item.duration,
            views=item.views,
            score=item.score,
        )
        for index, item in enumerate(results[:RESULT_COUNT])
    ]


async def search(query: str) -> list[SearchResult]:
    try:
        return await asyncio.to_thread(search_sync, query)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception:
        return []


def format_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_views(views: int | None) -> str:
    if views is None or views < 0:
        return ""
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)
