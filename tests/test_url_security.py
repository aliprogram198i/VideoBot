import io
import socket
import unittest
from urllib.request import Request

from downloader.url_security import (
    SafeRedirectHandler,
    redact_url,
    validate_public_http_url,
    read_limited,
)


class UrlSecurityTests(unittest.TestCase):
    def test_redact_url_removes_query_values_and_fragment(self):
        self.assertEqual(
            redact_url("https://example.com/media?id=123#frag"),
            "https://example.com/media?<redacted>",
        )

    def test_validate_rejects_non_public_addresses(self):
        def resolver(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

        with self.assertRaises(ValueError):
            validate_public_http_url("https://example.com", resolver=resolver)

    def test_validate_accepts_public_address(self):
        def resolver(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        parsed = validate_public_http_url("https://example.com/video", resolver=resolver)
        self.assertEqual(parsed.hostname, "example.com")

    def test_request_with_credentials_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_public_http_url(
                "https://user:password@example.com/video",
                resolver=lambda *args, **kwargs: [],
            )

    def test_read_limited_enforces_hard_limit(self):
        class Response:
            def __init__(self):
                self.stream = io.BytesIO(b"abcdef")

            def read(self, size):
                return self.stream.read(size)

        self.assertEqual(read_limited(Response(), 6), b"abcdef")
        with self.assertRaises(ValueError):
            read_limited(Response(), 5)

    def test_safe_redirect_handler_is_exported(self):
        self.assertTrue(issubclass(SafeRedirectHandler, object))
        self.assertEqual(Request("https://example.com").full_url, "https://example.com")


if __name__ == "__main__":
    unittest.main()
