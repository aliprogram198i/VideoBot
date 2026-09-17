import sys
import types
import unittest
from unittest.mock import patch

from telegram_layer.telegram_message_resolver import (
    TelegramResolverError,
    _metadata_matches,
    parse_telegram_message_url,
    resolve_telegram_message,
)


class TelegramMessageResolverTests(unittest.TestCase):
    def test_public_message_url_is_normalized(self):
        ref = parse_telegram_message_url("https://t.me/syrevarch/8453")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.channel, "syrevarch")
        self.assertEqual(ref.message_id, 8453)
        self.assertEqual(ref.canonical_url, "https://t.me/syrevarch/8453?single=1")

    def test_s_message_url_is_normalized(self):
        ref = parse_telegram_message_url("https://t.me/s/syrevarch/8453?foo=bar")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.canonical_url, "https://t.me/syrevarch/8453?single=1")

    def test_non_telegram_url_is_ignored(self):
        self.assertIsNone(parse_telegram_message_url("https://youtube.com/watch?v=x"))
        self.assertIsNone(parse_telegram_message_url("https://t.me/syrevarch"))

    def test_identity_rejects_wrong_message(self):
        ref = parse_telegram_message_url("https://t.me/syrevarch/8453")
        self.assertFalse(
            _metadata_matches(
                ref,
                {
                    "id": "8454",
                    "channel_id": "syrevarch",
                    "formats": [{"url": "https://cdn.example/video.mp4"}],
                },
            )
        )

    def test_identity_rejects_wrong_channel(self):
        ref = parse_telegram_message_url("https://t.me/syrevarch/8453")
        self.assertFalse(
            _metadata_matches(
                ref,
                {
                    "id": "8453",
                    "channel_id": "otherchannel",
                    "formats": [{"url": "https://cdn.example/video.mp4"}],
                },
            )
        )

    def test_playlist_result_is_rejected(self):
        ref = parse_telegram_message_url("https://t.me/syrevarch/8453")
        self.assertFalse(
            _metadata_matches(
                ref,
                {
                    "_type": "playlist",
                    "id": "syrevarch-8453",
                    "channel_id": "syrevarch",
                    "entries": [],
                },
            )
        )

    def test_preflight_uses_exact_identity(self):
        fake_info = {
            "id": "8453",
            "channel_id": "syrevarch",
            "webpage_url": "https://t.me/syrevarch/8453?single=1",
            "formats": [{"url": "https://cdn.example/video.mp4"}],
        }

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=False):
                self.url = url
                self.download = download
                return fake_info

        fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        with patch.dict(sys.modules, {"yt_dlp": fake_module}):
            canonical, info = __import__(
                "asyncio"
            ).run(
                resolve_telegram_message("https://t.me/syrevarch/8453")
            )

        self.assertEqual(canonical, "https://t.me/syrevarch/8453?single=1")
        self.assertEqual(info["id"], "8453")

    def test_preflight_rejects_wrong_identity(self):
        fake_info = {
            "id": "8454",
            "channel_id": "syrevarch",
            "formats": [{"url": "https://cdn.example/video.mp4"}],
        }

        class FakeYoutubeDL:
            def __init__(self, options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=False):
                return fake_info

        fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        with patch.dict(sys.modules, {"yt_dlp": fake_module}):
            with self.assertRaises(TelegramResolverError):
                __import__("asyncio").run(
                    resolve_telegram_message("https://t.me/syrevarch/8453")
                )


if __name__ == "__main__":
    unittest.main()
