import unittest

import bot

from downloader.production_stack import build_production_smart_extraction_stack


class RealHtmlMediaExtractionTests(unittest.TestCase):
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

    def test_real_html_media_extraction(self):
        source_url = (
            "https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/"
            "Elements/video"
        )

        result = self.stack.engine.extract(
            source_url,
            timeout=20,
            max_html_bytes=bot.MAX_HTML_BYTES,
            validation_timeout=10,
            max_ranked_candidates=20,
        )

        print("\n===== REAL HTML MEDIA EXTRACTION =====")
        print("source_url:", result.source_url)
        print("visited_pages:", result.visited_pages)
        print("candidate_count:", result.candidate_count)
        print("valid_candidate_count:", result.valid_candidate_count)
        print("invalid_candidate_count:", result.invalid_candidate_count)
        print("diagnostics:", result.diagnostics)

        if result.best_media:
            print("best_url:", result.best_media.candidate.url)
            print("best_kind:", result.best_media.candidate.kind)
            print("best_content_type:", result.best_media.content_type)
            print("best_status:", result.best_media.status)
        else:
            print("best_media: NONE")

        self.assertEqual(result.source_url, source_url)

        # The important assertion is that the production stack can process
        # the real HTML page without crashing.
        self.assertGreaterEqual(len(result.visited_pages), 1)


if __name__ == "__main__":
    unittest.main()
