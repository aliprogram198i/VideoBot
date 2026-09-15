"""Safe extraction of KRX18 public Video Sources.

Reads only bounded public HTML or the public WordPress REST representation of
the requested movie post when exposed. No authentication, challenge solving,
or access-control bypass is performed.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import quote_plus, urljoin, urlparse

SERVER_RE = re.compile(r"(?:server|سيرفر)\s*[-_ ]?\d+", re.I)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
TAG_RE = re.compile(r"<(?P<tag>a|iframe|embed|button|div|li|span)[^>]*?(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>", re.I | re.S)
ATTR_RE = re.compile(r"(?:href|src|data-server|data-player|data-download|data-url|data-href|onclick)\s*=\s*[\"']([^\"']+)[\"']", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DIRECT_MEDIA_RE = re.compile(r"\.(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)(?:$|[?#])", re.I)


def post_id_from_url(source_url: str) -> str | None:
    match = re.search(r"/movies/(\d+)(?:-|/)", str(source_url or ""), re.I)
    return match.group(1) if match else None


def _movie_slug_from_url(source_url: str) -> str:
    path = urlparse(str(source_url or "")).path.rstrip("/")
    match = re.search(r"/movies/(?:\d+-)?([^/]+)$", path, re.I)
    return match.group(1).replace("-", " ") if match else ""


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
        if "<" not in body and ">" not in body:
            for raw in URL_RE.findall(body):
                _add_target(ranked, raw, base_url, 100)

    anchor_re = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.I | re.S)
    for match in anchor_re.finditer(source_html):
        attrs = match.group("attrs") or ""
        body = match.group("body") or ""
        if not SERVER_RE.search(_clean_text(f"{attrs} {body}")):
            continue
        for href in re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", attrs, re.I):
            _add_target(ranked, href, base_url, 120)

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [url for url, _ in ordered[:max_targets]]


def extract_rest_search_candidates(data) -> list[dict]:
    """Normalize public WP search results without assuming a post type."""
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        result.append({
            "id": item_id,
            "type": str(item.get("type") or "").strip(),
            "subtype": str(item.get("subtype") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "title": _clean_text(str(item.get("title") or "")),
        })
    return result


def _rest_get(request_factory, open_function, read_function, endpoint: str, source_url: str, timeout: float, max_bytes: int):
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
    return json.loads(raw.decode("utf-8", "replace"))


def _rest_search_post(request_factory, open_function, read_function, source_url: str, timeout: float, max_bytes: int) -> tuple[str, list[str]]:
    """Use the public WP search API to discover the real movie post type/id."""
    movie_id = post_id_from_url(source_url)
    slug = _movie_slug_from_url(source_url)
    queries = [value for value in (slug[:120], movie_id) if value]
    seen: set[tuple[str, int]] = set()
    for query in queries:
        endpoint = (
            "https://krx18.com/wp-json/wp/v2/search?"
            f"search={quote_plus(query)}&per_page=10&_fields=id,type,subtype,url,title"
        )
        try:
            data = _rest_get(request_factory, open_function, read_function, endpoint, source_url, timeout, max_bytes)
        except Exception:
            continue
        for candidate in extract_rest_search_candidates(data):
            key = (candidate["type"], candidate["id"])
            if key in seen:
                continue
            seen.add(key)
            candidate_url = candidate["url"]
            candidate_slug = urlparse(candidate_url).path.rstrip("/").casefold()
            slug_match = bool(slug and slug.casefold().replace(" ", "-") in candidate_slug)
            id_match = bool(movie_id and movie_id == str(candidate["id"]))
            if not (slug_match or id_match):
                continue
            rest_base = candidate["subtype"] or candidate["type"]
            if rest_base in {"", "post", "page", "attachment"}:
                rest_base = "posts" if candidate["type"] == "post" else candidate["type"]
            if not rest_base or not re.fullmatch(r"[A-Za-z0-9_-]+", rest_base):
                continue
            detail_endpoint = f"https://krx18.com/wp-json/wp/v2/{rest_base}/{candidate['id']}?_fields=id,title,content,link"
            try:
                detail = _rest_get(request_factory, open_function, read_function, detail_endpoint, source_url, timeout, max_bytes)
            except Exception:
                continue
            if not isinstance(detail, dict):
                continue
            title = _clean_text(str(detail.get("title", {}).get("rendered", "")))
            content = str(detail.get("content", {}).get("rendered", ""))
            targets = extract_server_targets(content, source_url)
            if targets:
                return title or candidate["title"], targets
    return "", []


def _rest_candidate_types(request_factory, open_function, read_function, source_url: str, timeout: float, max_bytes: int) -> list[str]:
    """Discover movie-like public REST post types without guessing endpoints."""
    endpoint = "https://krx18.com/wp-json/wp/v2/types?_fields=slug,rest_base"
    try:
        data = _rest_get(request_factory, open_function, read_function, endpoint, source_url, timeout, max_bytes)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    ranked = []
    for value in data.values():
        if not isinstance(value, dict):
            continue
        rest_base = str(value.get("rest_base") or "").strip().strip("/")
        slug = str(value.get("slug") or "").casefold()
        base_lower = rest_base.casefold()
        if not rest_base:
            continue
        if any(token in slug or token in base_lower for token in ("movie", "film", "video")):
            ranked.append(rest_base)
    return list(dict.fromkeys(ranked))[:4]


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
    """Fetch bounded public KRX18 data and extract explicit server targets."""
    post_id = post_id_from_url(source_url)

    try:
        title, targets = _rest_search_post(
            request_factory, open_function, read_function,
            source_url, timeout, max_bytes,
        )
        if targets:
            return title, targets
    except Exception:
        pass

    if post_id:
        endpoints = []
        for rest_base in _rest_candidate_types(request_factory, open_function, read_function, source_url, timeout, max_bytes):
            endpoints.append(f"https://krx18.com/wp-json/wp/v2/{rest_base}/{post_id}?_fields=id,title,content,link")
        endpoints.append(f"https://krx18.com/wp-json/wp/v2/posts/{post_id}?_fields=id,title,content,link")
        for endpoint in endpoints[:5]:
            try:
                data = _rest_get(request_factory, open_function, read_function, endpoint, source_url, timeout, max_bytes)
                if isinstance(data, dict):
                    title = _clean_text(str(data.get("title", {}).get("rendered", "")))
                    content = str(data.get("content", {}).get("rendered", ""))
                    targets = extract_server_targets(content, source_url)
                    if targets:
                        return title, targets
            except Exception:
                continue

    try:
        return _extract_public_html(request_factory, open_function, read_function, source_url, timeout, max_bytes)
    except Exception:
        return "", []
