"""Telegram source identity primitives.

These helpers establish a stable identity for public Telegram post URLs and
prevent generic media resolvers from accepting a candidate that came from a
different page/post.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


_TELEGRAM_HOSTS = frozenset({
    "t.me",
    "telegram.me",
    "www.t.me",
    "www.telegram.me",
})


@dataclass(frozen=True)
class TelegramPostIdentity:
    """Canonical identity of one public Telegram channel post."""

    channel: str
    message_id: int

    @property
    def key(self) -> tuple[str, int]:
        return self.channel, self.message_id


def parse_telegram_post_url(url: str) -> TelegramPostIdentity | None:
    """Parse supported public Telegram post URL forms.

    Supported:
      https://t.me/channel/123
      https://t.me/s/channel/123
      https://telegram.me/channel/123

    Private/internal invite links and non-post Telegram URLs intentionally
    return None; they must not be treated as source-identifiable posts.
    """
    if not isinstance(url, str) or not url.strip():
        return None

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None

    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _TELEGRAM_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2:
        channel, message = parts
    elif len(parts) == 3 and parts[0].lower() == "s":
        _, channel, message = parts
    else:
        return None

    channel = channel.strip()
    if not channel or channel.startswith("+") or channel.startswith("joinchat"):
        return None

    if not message.isdigit():
        return None

    message_id = int(message)
    if message_id <= 0:
        return None

    return TelegramPostIdentity(
        channel=channel.lstrip("@").lower(),
        message_id=message_id,
    )


def telegram_post_identity(url: str) -> TelegramPostIdentity | None:
    """Compatibility alias for callers that prefer a noun-style API."""
    return parse_telegram_post_url(url)


def is_telegram_public_post_url(url: str) -> bool:
    """Return True only for a source URL with a parseable post identity."""
    return parse_telegram_post_url(url) is not None


def candidate_matches_telegram_source(
    candidate: Any,
    source_identity: TelegramPostIdentity,
) -> bool:
    """Accept a candidate only when its source page is the same Telegram post.

    A valid media URL alone is insufficient. The candidate must retain a
    source_page whose Telegram post identity exactly matches the requested
    channel and message ID.
    """
    if source_identity is None:
        return False

    source_page = getattr(candidate, "source_page", None)
    candidate_identity = parse_telegram_post_url(source_page)
    if candidate_identity is None:
        return False

    return candidate_identity.key == source_identity.key
