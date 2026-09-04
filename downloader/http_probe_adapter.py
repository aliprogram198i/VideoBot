"""Adapters connecting HTTPProbe to application network primitives.

The adapter translates a candidate probe request into the application's
existing HTTP opener contract. It deliberately does not import bot.py, so
the downloader package remains isolated from application startup side effects.
"""

from __future__ import annotations

from typing import Any, Callable

from .http_probe import HTTPProbe, ProbeResult


class HTTPProbeAdapter:
    """Expose an HTTPProbe through CandidateValidator's probe contract."""

    def __init__(self, probe: HTTPProbe) -> None:
        probe_method = getattr(probe, "probe", None)
        if not callable(probe_method):
            raise TypeError(
                "probe must provide a callable probe() method"
            )
        self._probe = probe

    def __call__(
        self,
        url: str,
        *,
        timeout: float = 15.0,
        kind: str | None = None,
    ) -> dict[str, Any]:
        result = self._probe.probe(
            url,
            timeout=timeout,
            kind=kind,
        )

        if not isinstance(result, ProbeResult):
            raise TypeError(
                "HTTPProbe.probe() must return ProbeResult"
            )

        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "url": result.url,
                "bytes_read": result.bytes_read,
                "reachable": result.reachable,
            }
        )

        if result.error is not None:
            metadata["probe_error"] = result.error

        return {
            "status": result.status,
            "content_type": result.content_type,
            "content_length": result.content_length,
            **metadata,
        }


class ProductionProbeOpener:
    """Translate HTTPProbe calls into the application's safe opener contract.

    ``request_factory`` creates the HTTP request object.
    ``open_function`` is expected to be the application's safe HTTP opener.
    ``max_declared_bytes`` is intentionally independent from HTTPProbe's
    small read limit.
    """

    def __init__(
        self,
        request_factory: Callable[..., Any],
        open_function: Callable[..., Any],
        *,
        max_declared_bytes: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not callable(request_factory):
            raise TypeError("request_factory must be callable")
        if not callable(open_function):
            raise TypeError("open_function must be callable")
        if max_declared_bytes <= 0:
            raise ValueError(
                "max_declared_bytes must be greater than zero"
            )

        self._request_factory = request_factory
        self._open_function = open_function
        self._max_declared_bytes = max_declared_bytes
        self._headers = dict(headers or {})

    def __call__(
        self,
        url: str,
        *,
        timeout: float,
        max_bytes: int,
        kind: str | None = None,
    ) -> Any:
        request = self._request_factory(
            url,
            headers=self._headers,
        )

        # max_bytes belongs to HTTPProbe's read limit. The application's
        # safe opener needs the larger declared-resource safety limit so
        # large media objects can still be probed without downloading them.
        return self._open_function(
            request,
            timeout=timeout,
            max_bytes=self._max_declared_bytes,
        )


def make_production_probe_adapter(
    url_validator: Callable[[str], Any],
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    *,
    max_declared_bytes: int,
    headers: dict[str, str] | None = None,
) -> HTTPProbeAdapter:
    """Build the production CandidateValidator probe adapter."""
    opener = ProductionProbeOpener(
        request_factory=request_factory,
        open_function=open_function,
        max_declared_bytes=max_declared_bytes,
        headers=headers,
    )

    probe = HTTPProbe(
        url_validator=url_validator,
        open_function=opener,
    )

    return HTTPProbeAdapter(probe)
