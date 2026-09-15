"""Deterministic KRX18 public-page adapter.

Only explicit Video Sources/server/player targets exposed by the public movie
page are considered. This module never guesses URLs and does not bypass
authentication, CAPTCHA, DRM, paywalls, or other access controls.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

NON_SOURCE_HOSTS = {
    "onclckbn.net",
    "cdn.jsdelivr.net",
    "galleryn1.vcmdiawe.com",
    "bkcdn.net",
}


def is_krx18_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host == "krx18.com" or host.endswith(".krx18.com")


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _host(value: str) -> str:
    try:
        return (urlparse(value).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _is_non_source_host(value: str) -> bool:
    host = _host(value)
    return any(host == suffix or host.endswith("." + suffix) for suffix in NON_SOURCE_HOSTS)


def _explicit_server_label(text: str, row: dict) -> bool:
    value = str(text or "").casefold()
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        return True
    for key in ("data_server", "data_player"):
        if isinstance(row.get(key), str) and row.get(key).strip():
            return True
    return bool(re.fullmatch(r"\s*(?:server|سيرفر)\s*\d*\s*", value))


def _score(text: str, href: str, row: dict) -> int:
    value = f"{text} {href}".casefold()
    score = 0
    if _explicit_server_label(text, row):
        score += 120
    if any(word in value for word in ("player", "watch", "source", "مشاهدة", "مشغل")):
        score += 18
    if re.search(r"(?:server|سيرفر)\s*[-_ ]?\d+", value):
        score += 40
    if any(token in value for token in ("embed", "iframe")):
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


def rank_targets(rows: list[dict], base_url: str, max_targets: int = 8) -> list[str]:
    """Return only explicit KRX18 Video Sources/server/player targets."""
    ranked: dict[str, int] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = " ".join(
            str(row.get(k) or "")
            for k in ("text", "attr", "onclick", "label", "data_server", "data_player")
        )
        if not _explicit_server_label(text, row):
            continue
        values: list[str] = []
        for key in (
            "href", "src", "data_server", "data_player", "data_download",
            "data_url", "data_href",
        ):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        values.extend(extract_urls_from_onclick(str(row.get("onclick") or "")))
        for href in values:
            if not is_http_url(href) or href == base_url:
                continue
            if _is_non_source_host(href):
                continue
            score = _score(text, href, row)
            if score <= 0:
                continue
            ranked[href] = max(score, ranked.get(href, 0))
    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, _ in ordered[:max_targets]]
