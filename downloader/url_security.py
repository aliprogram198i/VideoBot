"""Public URL and HTTP response security primitives.

This module is intentionally dependency-light so download/resolver layers can
reuse the same SSRF and response-size protections without importing bot.py.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


def redact_url(value):
    """Return a log-safe URL without credentials, query values, or fragments."""
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        return urlunparse(
            (
                parsed.scheme,
                host,
                parsed.path,
                "",
                "<redacted>" if parsed.query else "",
                "",
            )
        )
    except Exception:
        return "<invalid-url>"


def validate_public_http_url(value, resolver=socket.getaddrinfo):
    """Reject URLs that could target local or otherwise non-public services."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local hosts are not allowed")
    try:
        addresses = resolver(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError) as exc:
        raise ValueError("Host could not be resolved") from exc
    if not addresses:
        raise ValueError("Host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Non-public network addresses are not allowed")
    return parsed


class SafeRedirectHandler(HTTPRedirectHandler):
    """Validate every redirect destination before urllib follows it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(
    request,
    *,
    timeout,
    max_bytes,
    expected_content_types=None,
):
    """Open an external URL with SSRF and response-size protections."""
    target = request.full_url if isinstance(request, Request) else request
    validate_public_http_url(target)
    response = build_opener(SafeRedirectHandler()).open(
        request,
        timeout=timeout,
    )
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > max_bytes:
        response.close()
        raise ValueError("Response exceeds configured size limit")
    content_type = response.headers.get_content_type()
    if expected_content_types and content_type not in expected_content_types:
        response.close()
        raise ValueError("Unexpected response content type")
    return response


def read_limited(response, max_bytes):
    """Read a response while enforcing a hard byte limit."""
    chunks = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("Response exceeds configured size limit")
        chunks.append(chunk)
