"""Bounded HTTP probing for the smart extraction engine.

The probe collects lightweight HTTP metadata without downloading a complete
media object. URL validation, opening the connection, and reading bytes are
injected by the caller so the application's existing network-security layer
remains the single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ProbeResult:
    """HTTP metadata collected from one candidate URL."""

    url: str
    status: int | None
    content_type: str | None
    content_length: int | None
    bytes_read: int
    reachable: bool
    error: str | None = None
    metadata: dict[str, Any] | None = None


class HTTPProbe:
    """Perform bounded HTTP probes through injected network primitives."""

    def __init__(
        self,
        url_validator: Callable[[str], Any],
        open_function: Callable[..., Any],
    ) -> None:
        if not callable(url_validator):
            raise TypeError("url_validator must be callable")

        if not callable(open_function):
            raise TypeError("open_function must be callable")

        self._url_validator = url_validator
        self._open_function = open_function

    def probe(
        self,
        url: str,
        *,
        timeout: float = 15.0,
        max_bytes: int = 64 * 1024,
        kind: str | None = None,
    ) -> ProbeResult:
        """Probe one URL while reading at most ``max_bytes`` bytes."""

        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")

        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")

        try:
            self._url_validator(url)
        except Exception as exc:
            return ProbeResult(
                url=url,
                status=None,
                content_type=None,
                content_length=None,
                bytes_read=0,
                reachable=False,
                error=f"url_validation_failed:{type(exc).__name__}",
            )

        response = None

        try:
            response = self._open_function(
                url,
                timeout=timeout,
                max_bytes=max_bytes,
                kind=kind,
            )

            headers = getattr(response, "headers", None)

            status = getattr(response, "status", None)
            if status is None:
                status = getattr(response, "code", None)

            if status is not None:
                try:
                    status = int(status)
                except (TypeError, ValueError):
                    return ProbeResult(
                        url=url,
                        status=None,
                        content_type=None,
                        content_length=None,
                        bytes_read=0,
                        reachable=False,
                        error="invalid_status",
                    )

            content_type = None
            content_length = None

            if headers is not None:
                try:
                    content_type = headers.get("Content-Type")
                except Exception:
                    content_type = None

                try:
                    content_length = headers.get("Content-Length")
                except Exception:
                    content_length = None

            if content_length is not None:
                try:
                    content_length = int(content_length)
                except (TypeError, ValueError):
                    content_length = None

            bytes_read = 0

            if hasattr(response, "read"):
                data = response.read(max_bytes + 1)

                if data is not None:
                    bytes_read = len(data)

                if bytes_read > max_bytes:
                    return ProbeResult(
                        url=url,
                        status=status,
                        content_type=content_type,
                        content_length=content_length,
                        bytes_read=max_bytes,
                        reachable=True,
                        error="probe_response_exceeds_limit",
                    )

            reachable = status is None or 200 <= status < 400

            return ProbeResult(
                url=url,
                status=status,
                content_type=content_type,
                content_length=content_length,
                bytes_read=bytes_read,
                reachable=reachable,
                metadata={
                    "kind": kind,
                },
            )

        except Exception as exc:
            return ProbeResult(
                url=url,
                status=None,
                content_type=None,
                content_length=None,
                bytes_read=0,
                reachable=False,
                error=f"probe_failed:{type(exc).__name__}",
            )

        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
