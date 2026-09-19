"""Exact Facebook Reel source identity and candidate provenance checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


_REEL_ID_RE = re.compile(r"^\\d{5,30}$")
_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch"}


@dataclass(frozen=True)
class FacebookReelIdentity:
    reel_id: str
    source_url: str


def parse_facebook_reel_url(url: str) -> FacebookReelIdentity | None:
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _FACEBOOK_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    reel_id = ""
    if len(parts) >= 2 and parts[0].lower() == "reel":
        reel_id = parts[1]
    elif parsed.path.rstrip("/").lower() == "/watch":
        reel_id = (parse_qs(parsed.query).get("v") or [""])[0]
    if not _REEL_ID_RE.fullmatch(reel_id):
        return None
    return FacebookReelIdentity(reel_id=reel_id, source_url=url.strip())


def candidate_matches_facebook_reel(candidate: object, identity: FacebookReelIdentity) -> bool:
    if identity is None:
        return False
    metadata = getattr(candidate, "metadata", {}) or {}
    if str(metadata.get("facebook_reel_id", "")) == identity.reel_id:
        return True
    source_page = str(getattr(candidate, "source_page", "") or "")
    source_identity = parse_facebook_reel_url(source_page)
    return source_identity is not None and source_identity.reel_id == identity.reel_id


__all__ = ["FacebookReelIdentity", "candidate_matches_facebook_reel", "parse_facebook_reel_url"]
