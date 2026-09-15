"""Safe extraction of KRX18 public WordPress Video Sources.

Reads only the public WordPress REST representation of the requested movie
post when exposed. No authentication, challenge solving, or access-control
bypass is performed.
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
    """Extract only explicit Server N links from public WP content."""
    ranked: dict[str, int] = {}
    source_html = html.unescape(rendered_html or "")

    for href, label_html in ANCHOR_RE.findall(source_html):
        label = _clean_text(label_html)
        if not SERVER_RE.search(label):
            continue
        target = urljoin(base_url, html.unescape(href).strip())
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        score = 100
        value = f"{label} {target}".casefold()
        if any(token in value for token in ("player", "watch", "stream", "source")):
            score += 20
        ranked[target] = max(score, ranked.get(target, 0))

    # Some WordPress themes put the URL in an onclick/data attribute or plain
    # text. Accept it only when a Server N marker is immediately nearby.
    for value in URL_RE.findall(source_html):
        target = value.rstrip(".,;)]}")
        pos = source_html.find(value)
        nearby = source_html[max(0, pos - 400):pos + len(value) + 150]
        if not SERVER_RE.search(_clean_text(nearby)):
            continue
        parsed = urlparse(target)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            ranked[target] = max(80, ranked.get(target, 0))

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, _ in ordered[:max_targets]]


def fetch_public_post(
    source_url: str,
    *,
    request_factory,
    open_function,
    read_function,
    timeout: float = 7.0,
    max_bytes: int = 512 * 1024,
) -> tuple[str, list[str]]:
    """Fetch one bounded public WP post and extract its server targets."""
    post_id = post_id_from_url(source_url)
    if not post_id:
        return "", []

    endpoint = f"https://krx18.com/wp-json/wp/v2/posts/{post_id}?_fields=id,title,content,link"
    request = request_factory(
        endpoint,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AliBot-KRX18/1.0)",
            "Accept": "application/json",
            "Referer": source_url,
        },
        method="GET",
    )
    with open_function(request, timeout=timeout, max_bytes=max_bytes) as response:
        raw = read_function(response, max_bytes)
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        return "", []
    title = _clean_text(str(data.get("title", {}).get("rendered", "")))
    content = str(data.get("content", {}).get("rendered", ""))
    return title, extract_server_targets(content, source_url)
