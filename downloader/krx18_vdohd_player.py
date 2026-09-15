"""Bounded public VDOHD player evidence extraction for KRX18 Server 1.

This module only reads player configuration already exposed by a public page.
It does not solve challenges or bypass authentication, CAPTCHA, DRM, paywalls,
or other access controls.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlparse

VDOHD_HOSTS = {"vdohd.com", "www.vdohd.com"}
_MEDIA_EXT_RE = re.compile(r"\.(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)(?:$|[?#])", re.I)
_MEDIA_CONTEXT_RE = re.compile(
    r"(?:file|src|source|sources|playlist|media|video|stream|hls|dash)"
    r"\s*[:=]\s*[\"'](?P<url>[^\"']+)[\"']",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
_PROTOCOL_URL_RE = re.compile(r"(?<![\w])//[^\s\"'<>]+")
_BLOCKED_HOSTS = {
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "adservice.google.com", "onclckbn.net",
}
_BAD_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|gif|webp|svg|css|js)(?:$|[?#])", re.I)
_MAX_CONFIG_BODY_BYTES = 512 * 1024
_MAX_NETWORK_CONFIG_RESOURCES = 12
_NETWORK_CONFIG_TERMS = (
    "api", "ajax", "player", "config", "source", "playlist", "stream", "media", "video", "embed"
)
_TEXT_CONTENT_TYPES = (
    "text/", "application/json", "application/javascript", "text/javascript",
    "application/x-javascript", "application/vnd.apple.mpegurl",
)


def is_vdohd_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host in VDOHD_HOSTS or host.endswith(".vdohd.com")


def _normalise(raw: str, base_url: str) -> str | None:
    value = html.unescape(str(raw or "")).strip()
    value = value.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    value = value.rstrip(".,;)]}")
    if value.startswith("//"):
        value = "https:" + value
    value = urljoin(base_url, value)
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if any(host == bad or host.endswith("." + bad) for bad in _BLOCKED_HOSTS):
        return None
    if _BAD_EXT_RE.search(parsed.path) and not _MEDIA_EXT_RE.search(parsed.path):
        return None
    return value


def extract_vdohd_media_urls(text: str, base_url: str) -> list[str]:
    """Extract media URLs from player configuration, not arbitrary page links."""
    if not isinstance(text, str) or not text:
        return []
    text = html.unescape(text).replace("\\/", "/")
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        value = _normalise(raw, base_url)
        if not value or value in seen:
            return
        parsed = urlparse(value)
        haystack = f"{parsed.path}?{parsed.query}".lower()
        if not (_MEDIA_EXT_RE.search(parsed.path) or any(x in haystack for x in ("m3u8", "mpd", "/stream", "/media", "/playlist"))):
            return
        seen.add(value)
        found.append(value)

    for match in _MEDIA_CONTEXT_RE.finditer(text):
        add(match.group("url"))

    for match in _URL_RE.finditer(text):
        context = text[max(0, match.start() - 180):match.start()]
        if re.search(r"(?:file|src|source|playlist|media|video|stream|hls|dash)", context, re.I):
            add(match.group(0))

    for match in _PROTOCOL_URL_RE.finditer(text):
        context = text[max(0, match.start() - 180):match.start()]
        if re.search(r"(?:file|src|source|playlist|media|video|stream|hls|dash)", context, re.I):
            add(match.group(0))

    return found[:12]


def _looks_like_network_config(url: str, content_type: str) -> bool:
    try:
        parsed = urlparse(url)
        path_query = f"{parsed.path}?{parsed.query}".casefold()
    except Exception:
        return False
    ctype = str(content_type or "").casefold().split(";", 1)[0].strip()
    if any(ctype.startswith(prefix) for prefix in _TEXT_CONTENT_TYPES):
        return True
    return any(term in path_query for term in _NETWORK_CONFIG_TERMS)


async def _collect_network_config_media(page, base_url: str) -> list[str]:
    """Fetch a small bounded set of public player config resources already exposed by the page."""
    try:
        resource_names = await page.evaluate(
            """() => performance.getEntriesByType('resource').map(e => e.name || '')"""
        )
    except Exception:
        return []

    resources: list[str] = []
    seen: set[str] = set()
    for value in resource_names or []:
        if not isinstance(value, str) or value in seen or not value.startswith(("http://", "https://")):
            continue
        try:
            parsed = urlparse(value)
            host = (parsed.hostname or "").lower().rstrip(".")
        except Exception:
            continue
        if any(host == bad or host.endswith("." + bad) for bad in _BLOCKED_HOSTS):
            continue
        path_query = f"{parsed.path}?{parsed.query}".casefold()
        if not (is_vdohd_url(value) or any(term in path_query for term in _NETWORK_CONFIG_TERMS)):
            continue
        if not any(term in path_query for term in _NETWORK_CONFIG_TERMS) and not _MEDIA_EXT_RE.search(parsed.path):
            continue
        seen.add(value)
        resources.append(value)
        if len(resources) >= _MAX_NETWORK_CONFIG_RESOURCES:
            break

    result: list[str] = []
    seen_media: set[str] = set()
    for resource in resources:
        try:
            response = await page.request.get(resource, timeout=1800, fail_on_status_code=False)
            headers = response.headers
            content_type = headers.get("content-type", "")
            raw_length = headers.get("content-length")
            if raw_length:
                try:
                    if int(raw_length) > _MAX_CONFIG_BODY_BYTES:
                        continue
                except ValueError:
                    pass
            if not _looks_like_network_config(resource, content_type):
                continue
            body = await response.body()
            if len(body) > _MAX_CONFIG_BODY_BYTES:
                continue
            text = body.decode("utf-8", "replace")
            for value in extract_vdohd_media_urls(text, resource):
                if value not in seen_media:
                    seen_media.add(value)
                    result.append(value)
        except Exception:
            continue
    return result[:12]


async def collect_vdohd_public_player_media(page) -> list[str]:
    """Collect player media from the public VDOHD document, frames, and exposed config resources."""
    try:
        base_url = page.url
    except Exception:
        return []
    if not is_vdohd_url(base_url):
        return []

    texts: list[str] = []
    try:
        texts.extend(await page.locator("script").all_text_contents())
    except Exception:
        pass
    try:
        rows = await page.locator("video,audio,source,iframe").evaluate_all(
            """els => els.flatMap(el => [el.getAttribute('src') || '', el.getAttribute('data-src') || '', el.getAttribute('data-url') || '', el.getAttribute('data-file') || '', el.getAttribute('data-source') || ''])"""
        )
        texts.extend([str(x) for x in rows or [] if x])
    except Exception:
        pass
    try:
        resource_names = await page.evaluate("""() => performance.getEntriesByType('resource').map(e => e.name || '')""")
        texts.extend([str(x) for x in resource_names or [] if x])
    except Exception:
        pass

    result: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for value in extract_vdohd_media_urls(text, base_url):
            if value not in seen:
                seen.add(value)
                result.append(value)

    for value in await _collect_network_config_media(page, base_url):
        if value not in seen:
            seen.add(value)
            result.append(value)

    for frame in list(page.frames):
        if frame is page.main_frame:
            continue
        try:
            if not is_vdohd_url(frame.url):
                continue
            frame_texts = await frame.locator("script").all_text_contents()
            for text in frame_texts:
                for value in extract_vdohd_media_urls(text, frame.url):
                    if value not in seen:
                        seen.add(value)
                        result.append(value)
            try:
                frame_resources = await frame.evaluate("""() => performance.getEntriesByType('resource').map(e => e.name || '')""")
            except Exception:
                frame_resources = []
            for value in frame_resources or []:
                if isinstance(value, str) and _media_url_from_resource(value):
                    if value not in seen:
                        seen.add(value)
                        result.append(value)
        except Exception:
            continue

    return result[:12]


def _media_url_from_resource(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        haystack = f"{parsed.path}?{parsed.query}".casefold()
    except Exception:
        return False
    return bool(_MEDIA_EXT_RE.search(parsed.path) or any(token in haystack for token in ("m3u8", "mpd", "/stream", "/media", "/playlist")))
