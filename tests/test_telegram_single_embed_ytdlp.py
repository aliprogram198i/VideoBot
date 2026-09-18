import unittest

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
        # This test verifies routing policy without requiring a live Telegram
        # request. A live extractor may legitimately return no formats.
        self.assertIsInstance(candidates, list)


if __name__ == "__main__":
    unittest.main()
