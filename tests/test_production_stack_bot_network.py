import unittest

import bot

from downloader.production_stack import build_production_smart_extraction_stack
from downloader.smart_engine import SmartExtractionEngine


class ProductionStackBotNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = build_production_smart_extraction_stack(
            url_validator=bot.validate_public_http_url,
            request_factory=bot.Request,
            open_function=bot.safe_urlopen,
            read_function=bot.read_limited,
            max_html_bytes=bot.MAX_HTML_BYTES,
            max_declared_bytes=bot.MAX_VIDEO_DOWNLOAD_BYTES,
        )

    def test_stack_uses_real_bot_network_security(self):
        self.assertIsInstance(
            self.stack.engine,
            SmartExtractionEngine,
        )

    def test_real_public_html_page(self):
        result = self.stack.engine.extract(
            "https://example.com/",
            timeout=15,
            max_html_bytes=bot.MAX_HTML_BYTES,
            validation_timeout=5,
            max_ranked_candidates=20,
        )

        self.assertEqual(result.source_url, "https://example.com/")
        self.assertGreaterEqual(len(result.visited_pages), 1)
        self.assertIn("https://example.com/", result.visited_pages)

    def test_private_url_is_rejected_by_existing_security_layer(self):
        result = self.stack.engine.extract(
            "http://127.0.0.1/",
            timeout=5,
            max_html_bytes=bot.MAX_HTML_BYTES,
            validation_timeout=3,
            max_ranked_candidates=20,
        )

        self.assertIsNone(result.best_media)
        self.assertTrue(
            any(
                item.startswith("resolution_failed:")
                for item in result.diagnostics
            )
        )


if __name__ == "__main__":
    unittest.main()
