import unittest
from unittest.mock import patch

from downloader.embed_resolver import EmbedResolver
from downloader.page_fetcher import PageFetcher


class TelegramUniversalYtdlpGuardTests(unittest.TestCase):
    def test_public_telegram_post_skips_untrusted_universal_ytdlp(self):
        resolver = EmbedResolver(PageFetcher(lambda *args, **kwargs: None))
        candidates = []
        seen = set()

        with patch("downloader.embed_resolver.extract_with_yt_dlp") as mocked:
            resolver._add_universal_ytdlp_candidates(
                "https://t.me/syrevarch/8453",
                depth=0,
                all_candidates=candidates,
                seen_candidates=seen,
            )

        mocked.assert_not_called()
        self.assertEqual(candidates, [])

    def test_non_telegram_url_keeps_universal_ytdlp_path(self):
        resolver = EmbedResolver(PageFetcher(lambda *args, **kwargs: None))
        candidates = []
        seen = set()

        with patch("downloader.embed_resolver.extract_with_yt_dlp", return_value=[]) as mocked:
            resolver._add_universal_ytdlp_candidates(
                "https://example.com/video",
                depth=0,
                all_candidates=candidates,
                seen_candidates=seen,
            )

        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
