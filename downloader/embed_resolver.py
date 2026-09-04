"""Recursive HTML/embed resolution for the smart extraction engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from .page_fetcher import PageFetcher
from .smart_extractor import MediaCandidate, extract_candidates
from .threads_extractor import extract_threads_media
from .threads_ytdlp import extract_threads_with_yt_dlp


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
                if first_error is None:
                    first_error = exc
                continue

            visited.append(current_url)

            # Threads share links can redirect to a canonical /@user/post/... URL.
            # Keep current_url as the source identity so the share ID remains
            # available to the platform-specific dynamic resolver, while using
            # fetched.url as the base for media URLs discovered in the page.
            parsed_host = (urlparse(fetched.url).hostname or "").lower()
            is_threads = (
                parsed_host == "threads.com"
                or parsed_host.endswith(".threads.com")
                or parsed_host == "threads.net"
                or parsed_host.endswith(".threads.net")
            )

            if is_threads:
                # First ask the official installed yt-dlp plugin. It understands
                # the current Threads server-rendered payload and /share/<id>
                # URLs directly. This is deliberately limited to public URLs.
                try:
                    threads_ytdlp = extract_threads_with_yt_dlp(current_url)
                    for candidate in threads_ytdlp:
                        key = (candidate.url, candidate.kind)
                        if key in seen_candidates:
                            continue
                        seen_candidates.add(key)
                        all_candidates.append(
                            MediaCandidate(
                                url=candidate.url,
                                kind=candidate.kind,
                                source_page=current_url,
                                discovered_by=candidate.discovered_by,
                                depth=depth,
                                score=candidate.confidence,
                            )
                        )
                        if len(all_candidates) >= self.max_candidates:
                            break
                except Exception as exc:
                    log = __import__("logging").getLogger(__name__)
                    log.warning("Threads yt-dlp fallback error: %s", type(exc).__name__)

                try:
                    threads_candidates = extract_threads_media(
                        fetched.html,
                        fetched.url,
                        max_candidates=self.max_candidates,
                        source_url=current_url,
                    )
                    for candidate in threads_candidates:
                        key = (candidate.url, candidate.kind)
                        if key in seen_candidates:
                            continue
                        seen_candidates.add(key)
                        all_candidates.append(
                            MediaCandidate(
                                url=candidate.url,
                                kind=candidate.kind,
                                source_page=fetched.url,
                                discovered_by=candidate.discovered_by,
                                depth=depth,
                                score=candidate.confidence,
                            )
                        )
                        if len(all_candidates) >= self.max_candidates:
                            break
                except (TypeError, ValueError):
                    # Generic extraction remains available if platform parsing
                    # receives malformed/unexpected page data.
                    pass

            if len(all_candidates) >= self.max_candidates:
                break

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
