"""Deterministic relevance signals for discovered media candidates.

This module does not attempt to understand arbitrary page semantics. It only
applies conservative, content-agnostic signals that distinguish obvious
secondary/ad media from primary media when candidates compete.
"""

from __future__ import annotations

from urllib.parse import urlparse


# These markers are deliberately conservative. They identify common media
# endpoints used for advertisements/promotional clips without rejecting every
# short video on the internet.
_OBVIOUS_SECONDARY_MARKERS = (
    "/ads/",
    "/ad/",
    "/advert/",
    "/advertisement/",
    "/preroll/",
    "/pre-roll/",
    "/commercial/",
    "/banner/",
    "advertising",
    "doubleclick",
    "googlesyndication",
)

_PROMOTIONAL_MARKERS = (
    "/trailer/",
    "/teaser/",
    "/promo/",
    "/promos/",
    "trailer",
    "teaser",
    "promotional",
)


def _candidate_text(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.path} {parsed.query}".casefold()


def obvious_secondary_penalty(url: str) -> int:
    """Return a large penalty only for strongly identifiable secondary media."""
    value = _candidate_text(url)
    if any(marker in value for marker in _OBVIOUS_SECONDARY_MARKERS):
        return 100
    if any(marker in value for marker in _PROMOTIONAL_MARKERS):
        return 45
    return 0


def size_relevance_bonus(content_length: int | None) -> int:
    """Prefer substantial media when candidates compete, without a hard size gate."""
    if content_length is None or content_length <= 0:
        return 0
    if content_length < 256 * 1024:
        return -35
    if content_length < 1 * 1024 * 1024:
        return -20
    if content_length < 5 * 1024 * 1024:
        return -8
    if content_length >= 100 * 1024 * 1024:
        return 15
    if content_length >= 20 * 1024 * 1024:
        return 10
    if content_length >= 5 * 1024 * 1024:
        return 5
    return 0
