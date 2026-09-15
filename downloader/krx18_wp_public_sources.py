"""Public WordPress source extraction for KRX18.

This module reads only the public WordPress REST representation of the requested movie post when exposed. It never authenticates, solves challenges, or bypasses access controls.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin, urlparse

SERVER_RE = re.compile(r"(?:server|سيرفر)\s*[-_ ]?\d+", re.I)
ANCHOR_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


def post_id_from_url(source_url: str) -> str | None:
    match = re.search(r"/movies/(\d+)(?:-|/)", str(source_url or ""), re.I)
    return match.group(1) if match else None


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def extract_server_targets(rendered_html: str, base_url: str, max_targets: int = 3) -> list[str]:
    ranked: dict[str, int] = {}
    for href, label_html in ANCHOR_RE.findall(rendered_html or ""):
        label = _clean_text(label_html)
        value = f"{label} {href}".casefold()
        if not SERVER_RE.search(label) and not any(token in value for token in ("player", "watch", "stream", "source", "playkrx18", "mov18plus")):
            continue
        target = urljoin(base_url, html.unescape(href).strip())
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        score = 100 if SERVER_RE.search(label) else 0
        if any(token in value for token in ("player", "watch", "stream", "source")):
            score += 20
        ranked[target] = max(score, ranked.get(target, 0))
    for value in URL_RE.findall(html.unescape(rendered_html or "")):
        target = value.rstrip(".,;)]}")
        pos = rendered_html.find(value)
        nearby = rendered_html[max(0, pos - 300):pos + len(value) + 100]
        if not SERVER_RE.search(nearby):
            continue
        if urlparse(target).hostname:
            ranked[target] = max(60, ranked.get(target, 0))
    return [url for url, _ in sorted(ranked.items(), key=lambda item: (-item[1], item[0]))[:max_targets]]


def fetch_public_post(source_url: str, *, request_factory, open_function, read_function, max_bytes: int = 512 * 1024) -> tuple[str, list[str]]:
    post_id = post_id_from_url(source_url)
    if not post_id:
        return "", []
    endpoint = f"https://krx18.com/wp-json/wp/v2/posts/{post_id}?_fields=id,title,content,link"
    request = request_factory(endpoint, headers={
        "User-Agent": "Mozilla/5.0 (compatible; AliBot-KRX18/1.0)",
        "Accept": "application/json",
        "Referer": source_url,
    }, method="GET")
    with open_function(request, timeout=7, max_bytes=max_bytes) as response:
        raw = read_function(response, max_bytes)
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        return "", []
    title = _clean_text(str(data.get("title", {}).get("rendered", "")))
    content = str(data.get("content", {}).get("rendered", ""))
    return title, extract_server_targets(content, source_url)
