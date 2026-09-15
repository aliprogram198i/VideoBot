"""Safe extraction of KRX18 public Video Sources.

Reads only bounded public HTML or the public WordPress REST representation of
the requested movie post when exposed. No authentication, challenge solving,
or access-control bypass is performed.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin, urlparse

SERVER_RE = re.compile(r"(?:server|سيرفر)\s*[-_ ]?\d+", re.I)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
TAG_RE = re.compile(r"<(?P<tag>a|iframe|embed|button|div|li|span)[^>]*?(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>", re.I | re.S)
ATTR_RE = re.compile(r"(?:href|src|data-server|data-player|data-download|data-url|data-href|onclick)\s*=\s*[\"']([^\"']+)[\"']", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DIRECT_MEDIA_RE = re.compile(r"\.(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)(?:$|[?#])", re.I)


def post_id_from_url(source_url: str) -> str | None:
    match = re.search(r"/movies/(\d+)(?:-|/)", str(source_url or ""), re.I)
    return match.group(1) if match else None


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _add_target(ranked: dict[str, int], raw_target: str, base_url: str, score: int) -> None:
    target = html.unescape(str(raw_target or "")).strip()
    if not target or target.lower().startswith(("javascript:", "#", "mailto:")):
        return
    target = urljoin(base_url, target)
    target = target.rstrip(".,;)]}")
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return
    if DIRECT_MEDIA_RE.search(parsed.path) or DIRECT_MEDIA_RE.search(parsed.query):
        return
    ranked[target] = max(score, ranked.get(target, 0))


def extract_server_targets(rendered_html: str, base_url: str, max_targets: int = 3) -> list[str]:
    """Extract only explicit Server N links/attributes from public content."""
    ranked: dict[str, int] = {}
    source_html = html.unescape(rendered_html or "")

    # Prefer elements whose visible label or attributes explicitly identify a
    # server/player. This handles themes that use buttons, data-* attributes,
    # onclick handlers, or relative URLs instead of ordinary <a href> links.
    for match in TAG_RE.finditer(source_html):
        attrs = match.group("attrs") or ""
        body = match.group("body") or ""
        label = _clean_text(f"{attrs} {body}")
        if not SERVER_RE.search(label):
            continue
        for raw in ATTR_RE.findall(attrs):
            score = 115
            lower = f"{label} {raw}".casefold()
            if any(token in lower for token in ("player", "watch", "stream", "source", "embed", "iframe")):
                score += 20
            _add_target(ranked, raw, base_url, score)
        for raw in URL_RE.findall(body):
            _add_target(ranked, raw, base_url, 100)

    # Also inspect anchors even when their server label is supplied by a nested
    # element rather than the anchor's direct text.
    anchor_re = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.I | re.S)
    for match in anchor_re.finditer(source_html):
        attrs = match.group("attrs") or ""
        body = match.group("body") or ""
        if not SERVER_RE.search(_clean_text(f"{attrs} {body}")):
            continue
        for href in re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", attrs, re.I):
            _add_target(ranked, href, base_url, 120)

    # Last bounded fallback: an absolute URL is accepted only when a Server N
    # marker occurs nearby, and never when that URL is already a direct media
    # asset. This prevents unrelated ads/assets from becoming first-hop targets.
    for value in URL_RE.findall(source_html):
        target = value.rstrip(".,;)]}")
        if DIRECT_MEDIA_RE.search(target):
            continue
        pos = source_html.find(value)
        nearby = source_html[max(0, pos - 700):pos + len(value) + 250]
        if SERVER_RE.search(_clean_text(nearby)):
            _add_target(ranked, target, base_url, 80)

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, _ in ordered[:max_targets]]


def _extract_public_html(request_factory, open_function, read_function, source_url: str, timeout: float, max_bytes: int) -> tuple[str, list[str]]:
    request = request_factory(
        source_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AliBot-KRX18/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": source_url,
        },
        method="GET",
    )
    with open_function(request, timeout=timeout, max_bytes=max_bytes) as response:
        raw = read_function(response, max_bytes)
    text = raw.decode("utf-8", "replace")
    title_match = TITLE_RE.search(text)
    title = _clean_text(title_match.group(1)) if title_match else ""
    return title, extract_server_targets(text, source_url)


def fetch_public_post(
    source_url: str,
    *,
    request_factory,
    open_function,
    read_function,
    timeout: float = 7.0,
    max_bytes: int = 2 * 1024 * 1024,
) -> tuple[str, list[str]]:
    """Fetch bounded public KRX18 data and extract explicit server targets.

    WordPress REST is preferred because it is structured. If it is unavailable,
    the same requested public movie page is fetched directly with the same
    bounded network policy. This is not an access-control bypass.
    """
    post_id = post_id_from_url(source_url)
    if post_id:
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
        try:
            with open_function(request, timeout=timeout, max_bytes=max_bytes) as response:
                raw = read_function(response, max_bytes)
            data = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(data, dict):
                title = _clean_text(str(data.get("title", {}).get("rendered", "")))
                content = str(data.get("content", {}).get("rendered", ""))
                targets = extract_server_targets(content, source_url)
                if targets:
                    return title, targets
        except Exception:
            pass

    try:
        return _extract_public_html(request_factory, open_function, read_function, source_url, timeout, max_bytes)
    except Exception:
        return "", []
