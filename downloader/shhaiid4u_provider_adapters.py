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

    def referer(self, candidate: str) -> str:
        """Return a normal provider-origin Referer for direct media requests.

        Player discovery returns direct media/provider endpoints. Using the
        endpoint itself as Referer is not a valid browser navigation context
        for providers that validate hotlink headers, and can produce tiny HTML
        challenge/error bodies that look like media responses. Keep the
        handoff bounded and use only the provider origin as the normal
        navigation/referrer context.
        """
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
        provider_referer = self.referer(candidate)
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
