import unittest
from unittest.mock import patch

from downloader.embed_resolver import EmbedResolver


class TelegramSingleEmbedYtdlpTests(unittest.TestCase):
    def test_generic_telegram_post_is_still_blocked(self):
        class DummyFetcher:
            def fetch(self, *args, **kwargs):
                raise AssertionError("fetcher should not be called")

        resolver = EmbedResolver(DummyFetcher())
        candidates = []
        seen = set()
        resolver._add_universal_ytdlp_candidates(
            "https://t.me/syrevarch/8453",
            depth=0,
            all_candidates=candidates,
            seen_candidates=seen,
        )
        self.assertEqual(candidates, [])

    def test_single_embed_is_allowed_for_exact_message(self):
        class DummyFetcher:
            def fetch(self, *args, **kwargs):
                raise AssertionError("fetcher should not be called")

        resolver = EmbedResolver(DummyFetcher())
        candidates = []
        seen = set()
        resolver._add_universal_ytdlp_candidates(
            "https://t.me/syrevarch/8453?embed=1&single=1",
            depth=0,
            all_candidates=candidates,
            seen_candidates=seen,
        )
        with patch(
            "downloader.embed_resolver.extract_with_yt_dlp",
            return_value=[],
        ) as extractor:
            resolver._add_universal_ytdlp_candidates(
                "https://t.me/syrevarch/8453?embed=1&single=1",
                depth=0,
                all_candidates=candidates,
                seen_candidates=seen,
            )
        extractor.assert_called_once_with(
            "https://t.me/syrevarch/8453?embed=1&single=1"
        )


if __name__ == "__main__":
    unittest.main()
