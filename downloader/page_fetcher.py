"""Safe HTML page fetching abstraction for the smart extraction engine.

The fetch implementation is injected by the caller. This keeps the
extraction layer independent from the bot's SSRF/security implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


DEFAULT_MAX_HTML_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class FetchedPage:
    """Fetched HTML page with minimal metadata."""

    url: str
    html: str
    content_type: str | None = None
    status: int | None = None


class PageFetcher:
    """Fetch HTML through an injected, trusted HTTP implementation.

    The injected function must already enforce URL validation, redirect
    validation, response-size limits, and any other network security policy.
    """

    def __init__(
        self,
        fetch_function: Callable[..., FetchedPage],
    ) -> None:
        if not callable(fetch_function):
            raise TypeError("fetch_function must be callable")

        self._fetch_function = fetch_function

    def fetch(
        self,
        url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_HTML_BYTES,
    ) -> FetchedPage:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")

        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")

        result = self._fetch_function(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
        )

        if not isinstance(result, FetchedPage):
            raise TypeError("fetch_function must return FetchedPage")

        return result
