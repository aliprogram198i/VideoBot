"""Production wiring for the deterministic smart extraction engine.

This module assembles the existing extraction components without importing
bot.py. All application-specific network/security primitives are injected
by the caller.
"""

from __future__ import annotations

from typing import Any, Callable

from .candidate_ranker import CandidateRanker
from .candidate_validator import CandidateValidator
from .embed_resolver import EmbedResolver
from .page_fetcher import PageFetcher
from .production_network import SmartNetworkAdapter
from .production_page_fetcher import ProductionPageFetcher
from .smart_engine import SmartExtractionEngine


DEFAULT_MAX_HTML_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_DECLARED_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_CANDIDATES = 100

DEFAULT_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}


class ProductionSmartExtractionStack:
    """Fully wired production smart-extraction stack."""

    def __init__(
        self,
        *,
        engine: SmartExtractionEngine,
        page_fetcher: PageFetcher,
        resolver: EmbedResolver,
        network: SmartNetworkAdapter,
    ) -> None:
        if not isinstance(engine, SmartExtractionEngine):
            raise TypeError("engine must be a SmartExtractionEngine")
        if not isinstance(page_fetcher, PageFetcher):
            raise TypeError("page_fetcher must be a PageFetcher")
        if not isinstance(resolver, EmbedResolver):
            raise TypeError("resolver must be an EmbedResolver")
        if not isinstance(network, SmartNetworkAdapter):
            raise TypeError("network must be a SmartNetworkAdapter")

        self.engine = engine
        self.page_fetcher = page_fetcher
        self.resolver = resolver
        self.network = network


def build_production_smart_extraction_stack(
    *,
    url_validator: Callable[[str], Any],
    request_factory: Callable[..., Any],
    open_function: Callable[..., Any],
    read_function: Callable[..., bytes],
    max_html_bytes: int = DEFAULT_MAX_HTML_BYTES,
    max_declared_bytes: int = DEFAULT_MAX_DECLARED_BYTES,
    page_headers: dict[str, str] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> ProductionSmartExtractionStack:
    """Build the complete production smart-extraction stack."""

    if max_html_bytes <= 0:
        raise ValueError("max_html_bytes must be greater than zero")
    if max_declared_bytes <= 0:
        raise ValueError("max_declared_bytes must be greater than zero")

    headers = dict(page_headers or DEFAULT_PAGE_HEADERS)

    production_fetcher = ProductionPageFetcher(
        request_factory=request_factory,
        open_function=open_function,
        read_function=read_function,
        max_html_bytes=max_html_bytes,
        headers=headers,
    )

    page_fetcher = PageFetcher(production_fetcher)

    resolver = EmbedResolver(
        page_fetcher,
        max_depth=max_depth,
        max_pages=max_pages,
        max_candidates=max_candidates,
    )

    # Use the same browser-like request headers for candidate probes as for
    # page fetching. This is important for Meta/CDN media endpoints that can
    # behave differently when probed with a minimal/default User-Agent.
    network = SmartNetworkAdapter(
        url_validator=url_validator,
        request_factory=request_factory,
        open_function=open_function,
        max_declared_bytes=max_declared_bytes,
        headers=headers,
    )

    validator = CandidateValidator(
        url_validator=url_validator,
        probe_function=network.probe,
    )

    ranker = CandidateRanker()

    engine = SmartExtractionEngine(
        resolver=resolver,
        validator=validator,
        ranker=ranker,
    )

    return ProductionSmartExtractionStack(
        engine=engine,
        page_fetcher=page_fetcher,
        resolver=resolver,
        network=network,
    )
