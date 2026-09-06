"""Runtime compatibility for public Facebook share URLs.

Facebook's /share/v/, /share/r/, and /share/p/ URLs are wrappers. For some
public posts, an anonymous request is redirected through /login/?next=...
although the target itself is a normal public video/reel URL. This module
resolves that wrapper before yt-dlp sees it.

It also removes the application's legacy forced YouTube client profile from
yt-dlp commands. Current yt-dlp releases maintain their own YouTube client
selection and forcing ``android,web`` can select clients that are challenged
by YouTube, especially for audio extraction. The shim deliberately removes
only that exact application-level override and otherwise leaves yt-dlp's
maintained defaults untouched.

It never supplies credentials, cookies, or authentication and therefore does
not bypass private/restricted content. If a platform still requires login,
the normal failure path remains active.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


_FACEBOOK_SHARE_MARKERS = ("/share/v/", "/share/r/", "/share/p/")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


def _is_facebook_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.com" or host.endswith(".fb.com")


def is_facebook_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return _is_facebook_host(urlparse(value).hostname)
    except Exception:
        return False


def is_facebook_share_url(value: str) -> bool:
    if not is_facebook_url(value):
        return False
    try:
        path = urlparse(value).path.lower()
    except Exception:
        return False
    return any(marker in path for marker in _FACEBOOK_SHARE_MARKERS)


def _is_youtube_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host in _YOUTUBE_HOSTS


def _remove_legacy_youtube_client_override(command: list) -> list:
    """Remove only the bot's legacy ``youtube:player_client=android,web`` flag."""
    if not command or not _is_youtube_url(str(command[-1])):
        return command

    cleaned = []
    index = 0

    while index < len(command):
        if (
            command[index] == "--extractor-args"
            and index + 1 < len(command)
            and str(command[index + 1]).strip().lower()
            == "youtube:player_client=android,web"
        ):
            index += 2
            continue

        cleaned.append(command[index])
        index += 1

    return cleaned


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _location_from_response_or_error(response_or_error) -> str | None:
    headers = getattr(response_or_error, "headers", None)
    if headers is None:
        return None
    try:
        return headers.get("Location")
    except AttributeError:
        return None


def resolve_public_facebook_share_url(url: str) -> str:
    """Resolve a Facebook share wrapper without bypassing authentication."""
    if not is_facebook_share_url(url):
        return url

    opener = build_opener(_NoRedirectHandler())
    current = url

    for _ in range(5):
        request = Request(
            current,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/139.0 Mobile Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

        try:
            with opener.open(request, timeout=15) as response:
                location = _location_from_response_or_error(response)
                if not location:
                    return current
        except Exception as exc:
            location = _location_from_response_or_error(exc)
            if not location:
                return current

        current = urljoin(current, location)
        parsed = urlparse(current)

        if not _is_facebook_host(parsed.hostname):
            return url

        if parsed.path.lower().startswith("/login"):
            next_values = parse_qs(parsed.query).get("next", [])
            if not next_values:
                return url

            target = unquote(next_values[0])
            if not is_facebook_url(target):
                return url

            current = target
            continue

        if not is_facebook_share_url(current):
            return current

    return current


def install_yt_dlp_facebook_resolver() -> None:
    """Install a narrow subprocess shim for yt-dlp only."""
    original = asyncio.create_subprocess_exec
    if getattr(original, "_alibot_facebook_resolver", False):
        return

    async def wrapped(*args, **kwargs):
        command = list(args)

        if len(command) >= 4 and command[:3] == ["python", "-m", "yt_dlp"]:
            command = _remove_legacy_youtube_client_override(command)

            candidate = command[-1]
            if is_facebook_share_url(candidate):
                try:
                    resolved = await asyncio.to_thread(
                        resolve_public_facebook_share_url,
                        candidate,
                    )
                    if resolved and resolved != candidate:
                        command[-1] = resolved
                        if "--referer" not in command:
                            command[3:3] = [
                                "--referer",
                                "https://www.facebook.com/",
                            ]
                except Exception:
                    # Never block the normal downloader path on URL resolution.
                    pass

        return await original(*command, **kwargs)

    wrapped._alibot_facebook_resolver = True
    asyncio.create_subprocess_exec = wrapped
