"""Narrow runtime hook for KRX18 public WordPress source fallback.

Only augments the existing KRX18 resolver when the public movie page does not
expose its Video Sources section to the browser. It never authenticates,
solves challenges, or bypasses access controls.
"""
from __future__ import annotations

import urllib.request
from urllib.parse import urlparse

try:
    import downloader.krx18_resolver as _krx
    from downloader.krx18_wp_public_sources import fetch_public_post as _fetch_public_post

    _trusted_hosts: set[str] = set()
    _original_extract = _krx._extract_source_targets
    _original_identity = _krx.identity_score

    def _open(request, *, timeout, max_bytes):
        return urllib.request.urlopen(request, timeout=timeout)

    def _read(response, limit):
        return response.read(limit)

    async def _extract(page, base_url: str):
        targets = await _original_extract(page, base_url)
        if targets:
            for value in targets:
                host = (urlparse(value).hostname or "").lower().rstrip(".")
                if host:
                    _trusted_hosts.add(host)
            return targets

        try:
            title, public_targets = _fetch_public_post(
                base_url,
                request_factory=urllib.request.Request,
                open_function=_open,
                read_function=_read,
            )
            if public_targets:
                for value in public_targets:
                    host = (urlparse(value).hostname or "").lower().rstrip(".")
                    if host:
                        _trusted_hosts.add(host)
                print(
                    f"🌐 KRX18 Public API: discovered {len(public_targets)} public server target(s)",
                    flush=True,
                )
            return public_targets
        except Exception as exc:
            print(
                f"🛡️ KRX18 Public API: unavailable ({type(exc).__name__})",
                flush=True,
            )
            return []

    def _identity(source_url, evidence_text, evidence_url="", source_title=""):
        score = _original_identity(source_url, evidence_text, evidence_url, source_title)
        host = (urlparse(evidence_url).hostname or "").lower().rstrip(".")
        if score < 40 and host and any(
            host == trusted or host.endswith("." + trusted)
            for trusted in _trusted_hosts
        ):
            return 60
        return score

    _krx._extract_source_targets = _extract
    _krx.identity_score = _identity
except Exception:
    # Never prevent the bot from starting if the optional hook cannot load.
    pass
