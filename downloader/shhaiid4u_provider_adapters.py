"""Provider adapters for public Shhaiid4u server candidates.

Each adapter owns provider-specific normalization and hand-off policy while the
shared browser download handoff owns browser session, click, download, and
media verification mechanics.

No authentication bypass, CAPTCHA solving, DRM bypass, or access-control
workarounds are implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .browser_download_handoff import resolve_to_file

MAX_FILE_BYTES = 500 * 1024 * 1024
MIN_VIDEO_BYTES = 2 * 1024 * 1024
MIN_VIDEO_DURATION = 45.0


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().rstrip(".")
    except Exception:
        return ""


def _origin(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return parsed._replace(path="/", params="", query="", fragment="").geturl()
    except Exception:
        pass
    return url


def _normalize_streamtape(url: str) -> str:
    if _host(url) not in {"streamtape.com", "streamtape.net"}:
        return url
    parsed = urlparse(url)
    path = parsed.path.replace("/e/", "/v/", 1)
    return parsed._replace(path=path).geturl()


@dataclass(frozen=True)
class ProviderAdapter:
    hostname: str
    settle_ms: int
    normalizer: object | None = None

    def normalize(self, url: str) -> str:
        if self.normalizer is None:
            return url
        return self.normalizer(url)

    def referer(self, candidate: str, source_url: str) -> str:
        """Return the actual player-page context used to discover the candidate.

        Provider servers commonly validate the hotlink against the page that
        embedded/discovered the media URL. The Shhaiid4u source page is the
        correct browser navigation/referrer context; using the provider origin
        alone can yield a small HTML challenge/error response instead of media.
        Fall back to the provider origin only when the source URL is unusable.
        """
        if source_url:
            try:
                parsed = urlparse(source_url)
                if parsed.scheme in {"http", "https"} and parsed.netloc:
                    return source_url
            except Exception:
                pass
        return _origin(candidate)

    def resolve(
        self,
        url: str,
        *,
        output_dir: str,
        validator,
        is_audio: bool,
        max_file_bytes: int,
        source_url: str,
    ) -> str | None:
        candidate = self.normalize(url)
        provider_referer = self.referer(candidate, source_url)
        return resolve_to_file(
            candidate,
            output_dir,
            validator=validator,
            is_audio=is_audio,
            timeout_ms=45_000,
            settle_ms=self.settle_ms,
            max_file_bytes=min(max_file_bytes, MAX_FILE_BYTES),
            referer_url=provider_referer,
            min_video_bytes=0 if is_audio else MIN_VIDEO_BYTES,
            min_video_duration=0.0 if is_audio else MIN_VIDEO_DURATION,
        )


ADAPTERS = {
    "megaup.net": ProviderAdapter("megaup.net", settle_ms=7_000),
    "streamtape.com": ProviderAdapter(
        "streamtape.com", settle_ms=5_000, normalizer=_normalize_streamtape
    ),
}


def adapter_for(url: str) -> ProviderAdapter | None:
    host = _host(url)
    if host in ADAPTERS:
        return ADAPTERS[host]
    for name, adapter in ADAPTERS.items():
        if host.endswith("." + name):
            return adapter
    return None
