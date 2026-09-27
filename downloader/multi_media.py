"""Deterministic helpers for multi-media post/collection handling."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Any, Callable, Iterable


MAX_MULTI_MEDIA_ITEMS = 20


@dataclass(frozen=True)
class MultiMediaItem:
    index: int
    url: str
    title: str | None = None
    media_type: str = "video"
    duration: float | None = None
    thumbnail: str | None = None


def is_collection_candidate(url: str) -> bool:
    """Return True only for URL shapes commonly representing multi-item posts."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path = (parsed.path or "").lower().rstrip("/")
    except ValueError:
        return False

    if host == "instagram.com" or host.endswith(".instagram.com"):
        return path.startswith(("/p/", "/reel/"))
    if host == "facebook.com" or host.endswith(".facebook.com"):
        return path.startswith(("/reel/", "/share/", "/posts/", "/watch"))
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return bool(path) and ("/video/" in path or path.startswith("/photo/"))
    return False


def _media_type(entry: dict[str, Any], *, instagram_child: bool = False) -> str:
    value = str(entry.get("media_type") or "").lower()
    if value in {"video", "image", "audio"}:
        return value

    ext = str(entry.get("ext") or "").lower()
    if ext in {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic", "heif"}:
        return "image"
    if ext in {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"}:
        return "audio"
    if instagram_child and entry.get("duration") is None and not entry.get("vcodec") and not entry.get("acodec"):
        return "image"
    return "video"


def normalize_entries(
    entries: Iterable[Any],
    *,
    url_validator: Callable[[str], bool],
    max_items: int = MAX_MULTI_MEDIA_ITEMS,
    parent_url: str | None = None,
) -> list[MultiMediaItem]:
    """Normalize yt-dlp playlist entries and fail closed on unusable URLs."""
    if max_items <= 0:
        raise ValueError("max_items must be greater than zero")

    result: list[MultiMediaItem] = []
    seen: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        url = (
            entry.get("webpage_url")
            or entry.get("original_url")
            or entry.get("url")
        )
        instagram_child = False
        if isinstance(parent_url, str) and is_collection_candidate(parent_url):
            parsed_parent = urlparse(parent_url)
            parent_host = (parsed_parent.hostname or "").lower().removeprefix("www.")
            if parent_host == "instagram.com" or parent_host.endswith(".instagram.com"):
                instagram_child = True
                if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                    child_id = entry.get("id") or entry.get("display_id") or url
                    if isinstance(child_id, str):
                        child_id = child_id.strip().strip("/")
                    if child_id:
                        url = f"https://www.instagram.com/p/{child_id}/"
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        if url in seen or not url_validator(url):
            continue

        seen.add(url)
        duration = entry.get("duration")
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None

        result.append(
            MultiMediaItem(
                index=len(result),
                url=url,
                title=str(entry.get("title") or "").strip() or None,
                media_type=_media_type(entry, instagram_child=instagram_child),
                duration=duration,
                thumbnail=str(entry.get("thumbnail") or "").strip() or None,
            )
        )

        if len(result) >= max_items:
            break

    return result
