"""Facebook Reel media-candidate resolver.

This resolver discovers only real media URLs from the public source/embed HTML.
It never treats an embed page itself as downloadable media and never returns a
candidate unless its provenance is tied to the exact Reel ID.
"""

from __future__ import annotations

import time
from urllib.parse import quote

from .facebook_identity import candidate_matches_facebook_reel, parse_facebook_reel_url
from .smart_extractor import MediaCandidate, extract_candidates


DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_HTML_BYTES = 5 * 1024 * 1024


def is_facebook_reel_url(url: str) -> bool:
    return parse_facebook_reel_url(url) is not None


def _variants(url: str, reel_id: str) -> list[str]:
    encoded = quote(url, safe="")
    values = [
        url,
        f"https://m.facebook.com/watch/?v={reel_id}&_rdr",
        "https://www.facebook.com/plugins/video.php"
        f"?href={encoded}&show_text=false&width=560",
        "https://www.facebook.com/plugins/video.php"
        f"?href=https%3A%2F%2Fwww.facebook.com%2Fwatch%2F%3Fv%3D{reel_id}"
        "&show_text=false&width=560",
    ]
    return list(dict.fromkeys(values))


def resolve(
    source_url: str,
    *,
    validator,
    request_factory,
    open_function,
    read_function,
    timeout: float = DEFAULT_TIMEOUT,
    max_html_bytes: int = DEFAULT_MAX_HTML_BYTES,
) -> list[dict[str, object]]:
    identity = parse_facebook_reel_url(source_url)
    if identity is None:
        return []
    if timeout <= 0 or max_html_bytes <= 0:
        return []

    validator(source_url)
    deadline = time.monotonic() + timeout
    results: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for page_url in _variants(source_url, identity.reel_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        request_timeout = max(1.0, min(remaining, 7.0))
        try:
            request = request_factory(
                page_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": source_url,
                },
                method="GET",
            )
            with open_function(request, timeout=request_timeout, max_bytes=max_html_bytes) as response:
                html = read_function(response, max_bytes=max_html_bytes)
                final_url = getattr(response, "url", None) or page_url
        except Exception:
            continue

        if not isinstance(html, str) or not html:
            continue

        for candidate in extract_candidates(
            html,
            str(final_url),
            depth=0,
            max_candidates=50,
        ):
            if candidate.kind not in {"hls", "dash", "progressive"}:
                continue
            enriched = MediaCandidate(
                url=candidate.url,
                kind=candidate.kind,
                source_page=str(final_url),
                discovered_by=candidate.discovered_by,
                depth=candidate.depth,
                score=candidate.score + 35,
                metadata={
                    **candidate.metadata,
                    "facebook_reel_id": identity.reel_id,
                    "facebook_source_url": source_url,
                    "facebook_resolver": "facebook_media_resolver_v1",
                },
            )
            if not candidate_matches_facebook_reel(enriched, identity):
                continue
            key = (enriched.url, enriched.kind)
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "url": enriched.url,
                "kind": enriched.kind,
                "score": enriched.score,
                "source_page": enriched.source_page,
                "metadata": dict(enriched.metadata),
            })

    return results[:12]


__all__ = ["is_facebook_reel_url", "resolve"]
