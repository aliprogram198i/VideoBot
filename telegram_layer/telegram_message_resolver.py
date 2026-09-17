"""Strict resolver for Telegram message URLs.

Telegram message links are resolved separately from the generic downloader path.
The resolver verifies the exact channel/message identity with yt-dlp before the
legacy download pipeline is allowed to continue.  Callers can then put the
canonical ``?single=1`` URL back into the existing pipeline without changing
its format/quality selection logic.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_TELEGRAM_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
_MESSAGE_PATH_RE = re.compile(r"^/(?:s/)?(?P<channel>[^/]+)/(?P<message_id>[1-9][0-9]*)/?$")
_C_MESSAGE_PATH_RE = re.compile(r"^/c/(?P<channel>[0-9]+)/(?P<message_id>[1-9][0-9]*)/?$")


class TelegramResolverError(RuntimeError):
    """Raised when a Telegram URL cannot be proven to identify one message."""


@dataclass(frozen=True)
class TelegramMessageRef:
    channel: str
    message_id: int
    is_internal_channel: bool = False

    @property
    def canonical_url(self) -> str:
        if self.is_internal_channel:
            path = f"/c/{self.channel}/{self.message_id}"
        else:
            path = f"/{self.channel}/{self.message_id}"
        return "https://t.me" + path + "?single=1"


# Set only while the verified Telegram URL is being processed by the existing
# download callback.  The generic recovery stages use this to fail closed.
_TELEGRAM_STRICT_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "alibot_telegram_strict_mode", default=False
)


def telegram_strict_mode() -> bool:
    return _TELEGRAM_STRICT_MODE.get()


def parse_telegram_message_url(url: str) -> TelegramMessageRef | None:
    """Parse public Telegram message URLs without making a network request."""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None

    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if (parsed.hostname or "").lower() not in _TELEGRAM_HOSTS:
        return None

    match = _C_MESSAGE_PATH_RE.fullmatch(parsed.path)
    if match:
        return TelegramMessageRef(
            channel=match.group("channel"),
            message_id=int(match.group("message_id")),
            is_internal_channel=True,
        )

    match = _MESSAGE_PATH_RE.fullmatch(parsed.path)
    if not match:
        return None

    channel = match.group("channel")
    if channel.lower() == "c":
        return None

    return TelegramMessageRef(
        channel=channel,
        message_id=int(match.group("message_id")),
    )


def _canonical_without_single(url: str) -> str:
    parsed = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() != "single"]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", urlencode(query), ""))


def _metadata_matches(ref: TelegramMessageRef, info: dict) -> bool:
    if not isinstance(info, dict):
        return False
    if info.get("_type") in {"playlist", "multi_video"}:
        return False
    if str(info.get("id")) != str(ref.message_id):
        return False

    if ref.is_internal_channel:
        channel_id = str(info.get("channel_id") or "")
        if channel_id and channel_id.lstrip("-") not in {ref.channel, f"-100{ref.channel}"}:
            return False
    else:
        channel_id = str(info.get("channel_id") or info.get("uploader_id") or "")
        if channel_id and channel_id.lower() != ref.channel.lower():
            return False

    webpage_url = str(info.get("webpage_url") or info.get("original_url") or "")
    if webpage_url:
        webpage_ref = parse_telegram_message_url(webpage_url)
        if webpage_ref is not None:
            if webpage_ref.message_id != ref.message_id:
                return False
            if not ref.is_internal_channel and webpage_ref.channel.lower() != ref.channel.lower():
                return False

    formats = info.get("formats") or []
    direct_url = info.get("url")
    return bool(formats or direct_url)


def _extract_metadata(url: str) -> dict:
    """Run yt-dlp metadata extraction synchronously; caller runs it off-loop."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise TelegramResolverError("yt-dlp is unavailable") from exc

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise TelegramResolverError(
            f"Telegram message extraction failed: {type(exc).__name__}"
        ) from exc

    if not _metadata_matches(parse_telegram_message_url(url), info):
        raise TelegramResolverError("Telegram media identity verification failed")

    return info


async def resolve_telegram_message(url: str) -> tuple[str, dict]:
    """Verify a Telegram message and return its canonical single-message URL."""
    ref = parse_telegram_message_url(url)
    if ref is None:
        raise TelegramResolverError("Not a Telegram message URL")

    canonical = ref.canonical_url
    info = await asyncio.to_thread(_extract_metadata, canonical)
    return canonical, info


def enter_telegram_strict_mode():
    return _TELEGRAM_STRICT_MODE.set(True)


def exit_telegram_strict_mode(token) -> None:
    _TELEGRAM_STRICT_MODE.reset(token)


__all__ = [
    "TelegramMessageRef",
    "TelegramResolverError",
    "enter_telegram_strict_mode",
    "exit_telegram_strict_mode",
    "parse_telegram_message_url",
    "resolve_telegram_message",
    "telegram_strict_mode",
]
