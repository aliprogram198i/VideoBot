"""Deterministic URL variants for public post/comment/reply links.

Some platforms expose a comment/reply as a query parameter on the parent
media URL. When that happens, the media extractor should also try the parent
resource. This module only removes known navigation/comment parameters; it
never guesses credentials or bypasses access controls.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re


_COMMENT_QUERY_KEYS = {
    "comment_id",
    "commentid",
    "reply_id",
    "replyid",
    "reply_to",
    "replyto",
    "lc",
    "comment",
    "commentid",
}


def public_media_variants(url: str) -> list[str]:
    """Return the original URL plus conservative parent-media variants."""
    if not isinstance(url, str) or not url.strip():
        return []

    raw = url.strip()
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    results = [raw]

    query = parse_qsl(parts.query, keep_blank_values=True)
    filtered = [(key, value) for key, value in query if key.lower() not in _COMMENT_QUERY_KEYS]
    if filtered != query:
        results.append(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(filtered), parts.fragment)))

    # Reddit comment URLs encode the parent post in /comments/<post-id>/<slug>/.
    if host.endswith("reddit.com"):
        match = re.match(r"^(/(?:r/[^/]+/)?comments/[^/]+/[^/]+)(?:/[^/]+)?/?$", parts.path)
        if match:
            results.append(urlunsplit((parts.scheme, parts.netloc, match.group(1) + "/", "", "")))

    # Keep order deterministic and remove duplicates.
    return list(dict.fromkeys(results))
