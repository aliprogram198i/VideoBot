import unittest

from downloader.smart_media_bridge import _telegram_embed_urls


class TelegramEmbedSourceTests(unittest.TestCase):
    def test_public_post_generates_deterministic_embed_variants(self):
        urls = _telegram_embed_urls("https://t.me/syrevarch/8453")
        self.assertEqual(
            urls,
            [
                "https://t.me/syrevarch/8453?embed=1&single=1",
                "https://t.me/syrevarch/8453?embed=1&single=1&mode=tme",
                "https://t.me/syrevarch/8453?embed=1&mode=tme",
                "https://t.me/syrevarch/8453?embed=1",
                "https://t.me/s/syrevarch/8453?embed=1&single=1",
                "https://t.me/s/syrevarch/8453?embed=1&single=1&mode=tme",
                "https://t.me/s/syrevarch/8453?embed=1&mode=tme",
                "https://t.me/s/syrevarch/8453?embed=1",
            ],
        )

    def test_query_and_fragment_do_not_change_identity(self):
        self.assertEqual(
            _telegram_embed_urls("https://t.me/syrevarch/8453?single=1#x")[0],
            "https://t.me/syrevarch/8453?embed=1&single=1",
        )

    def test_non_post_telegram_url_is_not_resolved(self):
        self.assertEqual(_telegram_embed_urls("https://t.me/syrevarch"), [])


if __name__ == "__main__":
    unittest.main()
