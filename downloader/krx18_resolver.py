"""Deterministic KRX18 public-page adapter.

Discovers the public Video Sources/server/player targets exposed by a KRX18
movie page. This module does not bypass authentication, CAPTCHA, DRM,
paywalls, or other access controls.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

SERVER_WORDS = (
    "server", "servers", "player", "watch", "stream", "source",
    "سيرفر", "سيرفرات", "مشاهدة", "مشغل", "تشغيل",
)
MEDIA_WORDS = (
    "download", "direct", "stream", "player", "watch", "source",
    "تحميل", "تنزيل", "رابط التحميل",
)


def is_krx18_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host == "krx18.com" or host.endswith(".krx18.com")


def _score(text: str, href: str) -> int:
    value = f"{text} {href}".casefold()
    score = 0
    score += sum(18 for word in SERVER_WORDS if word.casefold() in value)
    score += sum(14 for word in MEDIA_WORDS if word.casefold() in value)
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        score += 24
    if any(token in value for token in ("embed", "iframe", "player")):
        score += 12
    return score


def extract_urls_from_onclick(value: str) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    urls = []
    for match in re.findall(r"https?://[^\s\"'<>\\]+", value, flags=re.I):
        candidate = match.rstrip("\\.,;)]}")
        if is_http_url(candidate):
            urls.append(candidate)
    return urls


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def rank_targets(rows: list[dict], base_url: str, max_targets: int = 8) -> list[str]:
    """Rank explicit server/player/download targets without guessing URLs."""
    base_host = (urlparse(base_url).hostname or "").lower()
    ranked: dict[str, int] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get(k) or "") for k in ("text", "attr", "onclick", "label"))
        values = []
        for key in ("href", "src", "data_server", "data_player", "data_download", "data_url", "data_href"):
            value = row.get(key)
            if isinstance(value, str) and value:
                values.append(value)
        values.extend(extract_urls_from_onclick(str(row.get("onclick") or "")))
        for href in values:
            if not is_http_url(href) or href == base_url:
                continue
            score = _score(text, href)
            host = (urlparse(href).hostname or "").lower()
            if host and host != base_host:
                score += 10
            if score <= 0:
                continue
            ranked[href] = max(score, ranked.get(href, 0))
    return [url for url, _ in sorted(ranked.items(), key=lambda item: (-item[1], item[0]))[:max_targets]]
