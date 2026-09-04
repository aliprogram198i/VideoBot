import unittest
from urllib.request import Request

from bot import (
    MAX_HTML_BYTES,
    read_limited,
    safe_urlopen,
    validate_public_http_url,
)
from downloader.production_network import build_smart_network_adapter


class ProductionNetworkIntegrationTests(unittest.TestCase):
    URL = "https://example.com/"

    def test_real_public_url_validation(self):
        validate_public_http_url(self.URL)

    def test_real_safe_html_fetch(self):
        request = Request(
            self.URL,
            headers={
                "User-Agent": "AliBot/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        response = safe_urlopen(
            request,
            timeout=15.0,
            max_bytes=MAX_HTML_BYTES,
            expected_content_types={
                "text/html",
                "application/xhtml+xml",
            },
        )

        try:
            body = read_limited(response, 64 * 1024)
        finally:
            response.close()

        self.assertGreater(len(body), 0)

    def test_real_production_probe_adapter(self):
        adapter = build_smart_network_adapter(
            url_validator=validate_public_http_url,
            request_factory=Request,
            open_function=safe_urlopen,
            max_declared_bytes=MAX_HTML_BYTES,
            headers={
                "User-Agent": "AliBot/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        result = adapter.probe(
            self.URL,
            timeout=15.0,
            kind="iframe",
        )

        self.assertIsInstance(result, dict)
        self.assertIn("status", result)
        self.assertIn("content_type", result)
        self.assertIn("reachable", result)
        self.assertTrue(result["reachable"])
        self.assertGreaterEqual(result["status"], 200)
        self.assertLess(result["status"], 400)


if __name__ == "__main__":
    unittest.main()
