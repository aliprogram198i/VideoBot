import unittest
from unittest.mock import patch

from downloader.embed_resolver import EmbedResolver
from downloader.page_fetcher import PageFetcher


class SourceIdentityRegressionTests(unittest.TestCase):
    def make_resolver(self):
        def fetch(*args, **kwargs):
            raise RuntimeError("not used")

        return EmbedResolver(PageFetcher(fetch))

    def test_telegram_post_never_uses_universal_ytdlp_fallback(self):
        resolver = self.make_resolver()
        candidates = []
        seen = set()

        with patch("downloader.embed_resolver.extract_with_yt_dlp") as extractor:
            resolver._add_universal_ytdlp_candidates(
                "https://t.me/example/8453",
                depth=0,
                all_candidates=candidates,
                seen_candidates=seen,
            )

        extractor.assert_not_called()
        self.assertEqual(candidates, [])

    def test_telegram_embed_single_mode_is_explicit_exception(self):
        resolver = self.make_resolver()
        candidates = []
        seen = set()

        with patch("downloader.embed_resolver.extract_with_yt_dlp", return_value=[]) as extractor:
            resolver._add_universal_ytdlp_candidates(
                "https://t.me/example/8453?embed=1&single=1",
                depth=0,
                all_candidates=candidates,
                seen_candidates=seen,
            )

        extractor.assert_called_once()
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
