#!/usr/bin/env python3
"""Read-only KRX18 Server Target probe for one public movie URL."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request

SOURCE_URL = "https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"
UA = "Mozilla/5.0 (compatible; AliBot-KRX18-Probe/1.0)"
TIMEOUT = 10
MAX_BYTES = 2 * 1024 * 1024
MEDIA_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)


def get(url: str, accept: str = "*/*") -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Referer": SOURCE_URL})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        data = response.read(MAX_BYTES + 1)[:MAX_BYTES]
        charset = response.headers.get_content_charset() or "utf-8"
        return response.status, response.geturl(), data.decode(charset, "replace")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def server_targets(content: str) -> list[str]:
    marker = re.compile(r"(?:server|سيرفر)\s*[-_ ]?\d+", re.I)
    source = html.unescape(content)
    for match in marker.finditer(source):
        segment = source[match.start():min(len(source), match.start() + 1800)]
        targets = []
        seen = set()
        for raw in MEDIA_RE.findall(segment):
            target = raw.rstrip(".,;)]}")
            parsed = urllib.parse.urlparse(target)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            if re.search(r"\.(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)(?:$|[?#])", target, re.I):
                continue
            if target not in seen:
                seen.add(target)
                targets.append(target)
        if targets:
            return targets[:3]
    return []


def main() -> int:
    print(f"SOURCE={SOURCE_URL}")
    parsed = urllib.parse.urlparse(SOURCE_URL)
    movie_match = re.search(r"/movies/(\d+)-([^/]+)/?$", parsed.path, re.I)
    movie_id = movie_match.group(1) if movie_match else ""
    slug = movie_match.group(2) if movie_match else ""
    query = urllib.parse.quote(slug.replace("-", " ")[:120])
    search_url = f"https://krx18.com/wp-json/wp/v2/search?search={query}&per_page=10&_fields=id,type,subtype,url,title"
    print(f"WP_SEARCH={search_url}")
    try:
        status, _, raw = get(search_url, "application/json")
        print(f"WP_SEARCH_STATUS={status}")
        data = json.loads(raw)
    except Exception as exc:
        print(f"WP_SEARCH_ERROR={type(exc).__name__}:{exc}")
        return 2

    candidates = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        item_url = str(item.get("url") or "")
        item_slug = urllib.parse.urlparse(item_url).path.rstrip("/").casefold()
        if (movie_id and item_id == movie_id) or (slug and slug.casefold() in item_slug):
            candidates.append(item)
    print(f"WP_MATCHES={len(candidates)}")
    if not candidates:
        return 3

    for item in candidates[:3]:
        print(f"WP_MATCH_ID={item.get('id')}")
        print(f"WP_MATCH_TYPE={item.get('type')}")
        print(f"WP_MATCH_SUBTYPE={item.get('subtype')}")
        print(f"WP_MATCH_URL={item.get('url')}")
        rest_base = str(item.get("subtype") or item.get("type") or "posts")
        if rest_base in {"post", "page", "attachment"}:
            rest_base = "posts" if item.get("type") == "post" else rest_base
        detail_url = f"https://krx18.com/wp-json/wp/v2/{rest_base}/{item.get('id')}?_fields=id,title,content,link"
        try:
            status, _, raw = get(detail_url, "application/json")
            detail = json.loads(raw)
            title = clean(str(detail.get("title", {}).get("rendered", ""))) if isinstance(detail, dict) else ""
            content = str(detail.get("content", {}).get("rendered", "")) if isinstance(detail, dict) else ""
            targets = server_targets(content)
            print(f"WP_DETAIL_STATUS={status}")
            print(f"WP_TITLE={title}")
            print(f"SERVER_TARGET_COUNT={len(targets)}")
            for index, target in enumerate(targets, 1):
                print(f"SERVER_TARGET_{index}={target}")
                try:
                    t_status, t_final, t_body = get(target, "text/html,application/xhtml+xml,*/*")
                    title_match = re.search(r"<title[^>]*>(.*?)</title>", t_body, re.I | re.S)
                    page_title = clean(title_match.group(1)) if title_match else ""
                    media = []
                    for value in MEDIA_RE.findall(html.unescape(t_body).replace("\\/", "/")):
                        value = value.rstrip(".,;)]}")
                        if re.search(r"(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)", value, re.I) and value not in media:
                            media.append(value)
                    print(f"TARGET_{index}_STATUS={t_status}")
                    print(f"TARGET_{index}_FINAL_URL={t_final}")
                    print(f"TARGET_{index}_TITLE={page_title}")
                    print(f"TARGET_{index}_MEDIA_URL_COUNT={len(media)}")
                    for mi, media_url in enumerate(media[:5], 1):
                        print(f"TARGET_{index}_MEDIA_{mi}={media_url}")
                except Exception as exc:
                    print(f"TARGET_{index}_ERROR={type(exc).__name__}:{exc}")
        except Exception as exc:
            print(f"WP_DETAIL_ERROR={type(exc).__name__}:{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
