"""Recursive HTML/embed resolution for the smart extraction engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from .page_fetcher import PageFetcher
from .smart_extractor import MediaCandidate, extract_candidates
from .source_url_variants import public_media_variants
from .threads_extractor import extract_threads_media
from .threads_ytdlp import extract_threads_with_yt_dlp
from .universal_ytdlp import extract_with_yt_dlp


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

    @staticmethod
    def _is_threads_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return (
            host == "threads.com"
            or host.endswith(".threads.com")
            or host == "threads.net"
            or host.endswith(".threads.net")
        )

    @staticmethod
    def _is_media_candidate(candidate: MediaCandidate) -> bool:
        return candidate.kind in {"hls", "dash", "progressive"}

    def _add_candidate(
        self,
        candidate: MediaCandidate,
        *,
        source_page: str,
        depth: int,
        all_candidates: list[MediaCandidate],
        seen_candidates: set[tuple[str, str]],
    ) -> None:
        key = (candidate.url, candidate.kind)
        if key in seen_candidates:
            return
        seen_candidates.add(key)
        all_candidates.append(
            MediaCandidate(
                url=candidate.url,
                kind=candidate.kind,
                source_page=source_page,
                discovered_by=candidate.discovered_by,
                depth=depth,
                score=candidate.score,
                metadata=dict(candidate.metadata),
            )
        )

    def _add_threads_ytdlp_candidates(
        self,
        source_url: str,
        *,
        depth: int,
        all_candidates: list[MediaCandidate],
        seen_candidates: set[tuple[str, str]],
    ) -> None:
        """Run the isolated Threads yt-dlp plugin independently of page fetching."""
        try:
            threads_ytdlp = extract_threads_with_yt_dlp(source_url)
        except Exception as exc:
            log = __import__("logging").getLogger(__name__)
            log.warning("Threads yt-dlp fallback error: %s", type(exc).__name__)
            return

        for candidate in threads_ytdlp:
            self._add_candidate(
                MediaCandidate(
                    url=candidate.url,
                    kind=candidate.kind,
                    source_page=source_url,
                    discovered_by=candidate.discovered_by,
                    depth=depth,
                    score=candidate.confidence,
                    metadata={},
                ),
                source_page=source_url,
                depth=depth,
                all_candidates=all_candidates,
                seen_candidates=seen_candidates,
            )
            if len(all_candidates) >= self.max_candidates:
                break

    def _add_universal_ytdlp_candidates(
        self,
        source_url: str,
        *,
        depth: int,
        all_candidates: list[MediaCandidate],
        seen_candidates: set[tuple[str, str]],
    ) -> None:
        """Use the installed yt-dlp registry as the broad final extraction layer."""
        for variant in public_media_variants(source_url):
            try:
                extracted = extract_with_yt_dlp(variant)
            except Exception as exc:
                log = __import__("logging").getLogger(__name__)
                log.warning(
                    "Universal yt-dlp fallback error for %s: %s",
                    variant,
                    type(exc).__name__,
                )
                continue

            for item in extracted:
                self._add_candidate(
                    MediaCandidate(
                        url=item.url,
                        kind=item.kind,
                        source_page=source_url,
                        discovered_by=item.discovered_by,
                        depth=depth,
                        score=item.confidence,
                        metadata=dict(item.metadata),
                    ),
                    source_page=source_url,
                    depth=depth,
                    all_candidates=all_candidates,
                    seen_candidates=seen_candidates,
                )
                if len(all_candidates) >= self.max_candidates:
                    return

            # A successful extraction on the original URL is sufficient; only
            # use parent/comment variants when the original URL yielded nothing.
            if extracted:
                return

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

            if self._is_threads_url(current_url):
                self._add_threads_ytdlp_candidates(
                    current_url,
                    depth=depth,
                    all_candidates=all_candidates,
                    seen_candidates=seen_candidates,
                )
                if len(all_candidates) >= self.max_candidates:
                    break

            try:
                fetched = self.page_fetcher.fetch(
                    current_url,
                    timeout=timeout,
                    max_bytes=max_html_bytes,
                )
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                # Even when HTML fetching fails, a native yt-dlp extractor may
                # still know how to resolve the public media URL.
                if not self._is_threads_url(current_url):
                    self._add_universal_ytdlp_candidates(
                        current_url,
                        depth=depth,
                        all_candidates=all_candidates,
                        seen_candidates=seen_candidates,
                    )
                continue

            visited.append(current_url)

            parsed_host = (urlparse(fetched.url).hostname or "").lower()
            is_threads = (
                parsed_host == "threads.com"
                or parsed_host.endswith(".threads.com")
                or parsed_host == "threads.net"
                or parsed_host.endswith(".threads.net")
            )

            if is_threads:
                try:
                    threads_candidates = extract_threads_media(
                        fetched.html,
                        fetched.url,
                        max_candidates=self.max_candidates,
                        source_url=current_url,
                    )
                    for candidate in threads_candidates:
                        self._add_candidate(
                            candidate,
                            source_page=fetched.url,
                            depth=depth,
                            all_candidates=all_candidates,
                            seen_candidates=seen_candidates,
                        )
                        if len(all_candidates) >= self.max_candidates:
                            break
                except (TypeError, ValueError):
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
                self._add_candidate(
                    candidate,
                    source_page=fetched.url,
                    depth=depth,
                    all_candidates=all_candidates,
                    seen_candidates=seen_candidates,
                )

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

            # The page may contain no explicit media because the player is
            # generated by JavaScript or represented only in platform metadata.
            # Let yt-dlp's extractor registry have a chance before declaring
            # the source unresolved. Threads already has its dedicated path.
            if not is_threads and not any(
                self._is_media_candidate(candidate)
                for candidate in all_candidates
                if candidate.source_page == fetched.url
            ):
                self._add_universal_ytdlp_candidates(
                    current_url,
                    depth=depth,
                    all_candidates=all_candidates,
                    seen_candidates=seen_candidates,
                )

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
