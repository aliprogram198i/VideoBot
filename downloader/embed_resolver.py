"""Recursive HTML/embed resolution for the smart extraction engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .page_fetcher import PageFetcher
from .smart_extractor import MediaCandidate, extract_candidates


DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_CANDIDATES = 100


@dataclass(frozen=True)
class ResolutionResult:
    """Candidates discovered across the page/embed graph."""

    candidates: tuple[MediaCandidate, ...]
    visited_pages: tuple[str, ...]
    resolution_error: str | None = None


class EmbedResolver:
    """Resolve media and nested iframe candidates without executing JavaScript."""

    def __init__(
        self,
        page_fetcher: PageFetcher,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ) -> None:
        if not isinstance(page_fetcher, PageFetcher):
            raise TypeError("page_fetcher must be a PageFetcher")

        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        if max_pages <= 0:
            raise ValueError("max_pages must be > 0")

        if max_candidates <= 0:
            raise ValueError("max_candidates must be > 0")

        self.page_fetcher = page_fetcher
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.max_candidates = max_candidates

    def resolve(
        self,
        page_url: str,
        *,
        timeout: float = 30.0,
        max_html_bytes: int = 5 * 1024 * 1024,
    ) -> ResolutionResult:
        """Fetch and recursively inspect a page and its iframe/embed children."""

        queue: list[tuple[str, int]] = [(page_url, 0)]
        queued: set[str] = {page_url}
        visited: list[str] = []
        all_candidates: list[MediaCandidate] = []
        seen_candidates: set[tuple[str, str]] = set()
        first_error: Exception | None = None

        while queue and len(visited) < self.max_pages:
            current_url, depth = queue.pop(0)

            if current_url in visited:
                continue

            if depth > self.max_depth:
                continue

            try:
                fetched = self.page_fetcher.fetch(
                    current_url,
                    timeout=timeout,
                    max_bytes=max_html_bytes,
                )
            except Exception as exc:
                # A failed branch must not prevent other independent embeds
                # from being inspected. Record only the first failure so a
                # total resolution failure can be diagnosed by the caller.
                if first_error is None:
                    first_error = exc
                continue

            visited.append(current_url)

            candidates = extract_candidates(
                fetched.html,
                fetched.url,
                depth=depth,
                max_candidates=self.max_candidates,
            )

            for candidate in candidates:
                key = (candidate.url, candidate.kind)

                if key not in seen_candidates:
                    seen_candidates.add(key)
                    all_candidates.append(candidate)

                if (
                    candidate.kind == "iframe"
                    and depth < self.max_depth
                    and candidate.url not in queued
                    and candidate.url not in visited
                    and len(visited) + len(queue) < self.max_pages
                ):
                    queued.add(candidate.url)
                    queue.append((candidate.url, depth + 1))

                if len(all_candidates) >= self.max_candidates:
                    break

            if len(all_candidates) >= self.max_candidates:
                break

        # Media sources should rank before iframe discovery nodes.
        all_candidates.sort(
            key=lambda item: (
                -item.score,
                item.depth,
                item.url,
            )
        )

        resolution_error = None
        if not visited and first_error is not None:
            resolution_error = type(first_error).__name__

        return ResolutionResult(
            candidates=tuple(all_candidates[: self.max_candidates]),
            visited_pages=tuple(visited),
            resolution_error=resolution_error,
        )
