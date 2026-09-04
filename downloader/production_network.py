"""Production network adapter for the smart extraction engine.

The adapter receives the application's existing network-security primitives
from the caller. It does not import bot.py and therefore has no application
startup side effects.
"""

from __future__ import annotations

from typing import Any, Callable

from .http_probe_adapter import make_production_probe_adapter


DEFAULT_PROBE_READ_BYTES = 64 * 1024
DEFAULT_PROBE_TIMEOUT = 15.0


class SmartNetworkAdapter:
    """Build the production HTTP probe used by smart extraction."""

    def __init__(
        self,
        *,
        url_validator: Callable[[str], Any],
        request_factory: Callable[..., Any],
        open_function: Callable[..., Any],
        max_declared_bytes: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not callable(url_validator):
            raise TypeError("url_validator must be callable")
        if not callable(request_factory):
            raise TypeError("request_factory must be callable")
        if not callable(open_function):
            raise TypeError("open_function must be callable")
        if max_declared_bytes <= 0:
            raise ValueError(
                "max_declared_bytes must be greater than zero"
            )

        self._probe_adapter = make_production_probe_adapter(
            url_validator=url_validator,
            request_factory=request_factory,
            open_function=open_function,
            max_declared_bytes=max_declared_bytes,
            headers=headers,
        )

    @property
    def probe(self) -> Callable[..., dict[str, Any]]:
        """Return the probe callable expected by CandidateValidator."""
        return self._probe_adapter


def build_smart_network_adapter(
    *,
    url_validator: Callable[[str], Any],
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    max_declared_bytes: int,
    headers: dict[str, str] | None = None,
) -> SmartNetworkAdapter:
    """Construct a production smart-network adapter."""
    return SmartNetworkAdapter(
        url_validator=url_validator,
        request_factory=request_factory,
        open_function=open_function,
        max_declared_bytes=max_declared_bytes,
        headers=headers,
    )
