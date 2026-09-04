"""Production HTML page fetcher for the smart extraction engine.

This adapter bridges PageFetcher with the application's existing
URL-validation and safe HTTP-opening primitives. It does not import bot.py;
all security/network behavior is injected by the caller.
"""

from __future__ import annotations

import time

from typing import Any, Callable

from .page_fetcher import FetchedPage


DEFAULT_HTML_CONTENT_TYPES = frozenset({
    "text/html",
    "application/xhtml+xml",
})


class _DeadlineResponseProxy:
    """Expose a response while enforcing a total read deadline."""

    def __init__(self, response: Any, deadline: float) -> None:
        self._response = response
        self._deadline = deadline
        self._socket = self._find_socket(response)
        self._original_socket_timeout = (
            self._socket.gettimeout()
            if self._socket is not None
            else None
        )

    @staticmethod
    def _find_socket(response: Any) -> Any | None:
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None) if fp is not None else None
        sock = getattr(raw, "_sock", None) if raw is not None else None

        if sock is None:
            return None

        gettimeout = getattr(sock, "gettimeout", None)
        settimeout = getattr(sock, "settimeout", None)

        if not callable(gettimeout) or not callable(settimeout):
            return None

        return sock

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()

        if remaining <= 0:
            raise TimeoutError("HTML page fetch deadline exceeded")

        if self._socket is not None:
            try:
                self._socket.settimeout(remaining)
            except OSError:
                pass

        return remaining

    def read(self, size: int = -1) -> bytes:
        self._remaining()
        return self._response.read(size)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def restore_socket_timeout(self) -> None:
        if self._socket is not None:
            try:
                self._socket.settimeout(self._original_socket_timeout)
            except OSError:
                pass


class ProductionPageFetcher:
    """Fetch bounded HTML pages through injected production primitives."""

    def __init__(
        self,
        *,
        request_factory: Callable[..., Any],
        open_function: Callable[..., Any],
        read_function: Callable[..., bytes],
        max_html_bytes: int,
        headers: dict[str, str] | None = None,
        expected_content_types: set[str] | frozenset[str] | None = None,
    ) -> None:
        if not callable(request_factory):
            raise TypeError("request_factory must be callable")
        if not callable(open_function):
            raise TypeError("open_function must be callable")
        if not callable(read_function):
            raise TypeError("read_function must be callable")
        if max_html_bytes <= 0:
            raise ValueError("max_html_bytes must be greater than zero")

        self._request_factory = request_factory
        self._open_function = open_function
        self._read_function = read_function
        self.max_html_bytes = max_html_bytes
        self.headers = dict(headers or {})
        self.expected_content_types = frozenset(
            expected_content_types or DEFAULT_HTML_CONTENT_TYPES
        )

    def __call__(
        self,
        url: str,
        *,
        timeout: float,
        max_bytes: int,
    ) -> FetchedPage:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")

        effective_max_bytes = min(max_bytes, self.max_html_bytes)

        request = self._request_factory(
            url,
            headers=self.headers,
        )

        response = self._open_function(
            request,
            timeout=timeout,
            max_bytes=effective_max_bytes,
            expected_content_types=set(self.expected_content_types),
        )

        try:
            status = getattr(response, "status", None)
            if status is None:
                status = getattr(response, "code", None)

            content_type = None
            headers = getattr(response, "headers", None)
            if headers is not None:
                try:
                    content_type = headers.get("Content-Type")
                except AttributeError:
                    content_type = None

            deadline = time.monotonic() + timeout
            deadline_response = _DeadlineResponseProxy(
                response,
                deadline,
            )

            try:
                body = self._read_function(
                    deadline_response,
                    effective_max_bytes,
                )
            finally:
                deadline_response.restore_socket_timeout()

            if not isinstance(body, bytes):
                raise TypeError("read_function must return bytes")

            charset = "utf-8"
            if content_type and "charset=" in content_type.lower():
                charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip()
                charset = charset.strip("\"'") or "utf-8"

            try:
                html = body.decode(charset, errors="replace")
            except (LookupError, TypeError):
                html = body.decode("utf-8", errors="replace")

            return FetchedPage(
                url=url,
                html=html,
                content_type=content_type,
                status=status,
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()


def build_production_page_fetcher(
    *,
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    read_function: Callable[..., bytes],
    max_html_bytes: int,
    headers: dict[str, str] | None = None,
    expected_content_types: set[str] | frozenset[str] | None = None,
) -> ProductionPageFetcher:
    """Build a production page-fetch callable for PageFetcher."""
    return ProductionPageFetcher(
        request_factory=request_factory,
        open_function=open_function,
        read_function=read_function,
        max_html_bytes=max_html_bytes,
        headers=headers,
        expected_content_types=expected_content_types,
    )
