import unittest

from downloader.smart_extractor import MediaCandidate
from downloader.telegram_identity import (
    candidate_matches_telegram_source,
    is_telegram_public_post_url,
    parse_telegram_post_url,
)


class TelegramIdentityTests(unittest.TestCase):
    def test_parses_canonical_public_post(self):
        identity = parse_telegram_post_url("https://t.me/example/8453?single")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.key, ("example", 8453))

    def test_parses_s_form(self):
        identity = parse_telegram_post_url("https://t.me/s/example/8453")
        self.assertEqual(identity.key, ("example", 8453))

    def test_parses_telegram_me_alias(self):
        identity = parse_telegram_post_url("https://telegram.me/example/8453")
        self.assertEqual(identity.key, ("example", 8453))

    def test_rejects_non_post_urls(self):
        for url in (
            "https://t.me/example",
            "https://t.me/example?start=1",
            "https://t.me/+invite",
            "https://example.com/example/8453",
            "https://t.me/c/123456/8453",
        ):
            self.assertIsNone(parse_telegram_post_url(url))
            self.assertFalse(is_telegram_public_post_url(url))

    def test_matching_source_identity_is_required(self):
        source = parse_telegram_post_url("https://t.me/example/8453")
        same = MediaCandidate(
            url="https://cdn.example/video.mp4",
            kind="progressive",
            source_page="https://t.me/s/example/8453",
            discovered_by="video",
            metadata={"telegram_data_post": "example/8453"},
        )
        neighboring = MediaCandidate(
            url="https://cdn.example/other.mp4",
            kind="progressive",
            source_page="https://t.me/example/8454",
            discovered_by="video",
        )
        foreign = MediaCandidate(
            url="https://cdn.example/foreign.mp4",
            kind="progressive",
            source_page="https://example.com/watch",
            discovered_by="video",
        )

        self.assertTrue(candidate_matches_telegram_source(same, source))
        self.assertFalse(candidate_matches_telegram_source(neighboring, source))

        missing_provenance = MediaCandidate(
            url="https://cdn.example/unverified.mp4",
            kind="progressive",
            source_page="https://t.me/example/8453",
            discovered_by="video",
        )
        wrong_provenance = MediaCandidate(
            url="https://cdn.example/wrong.mp4",
            kind="progressive",
            source_page="https://t.me/example/8453",
            discovered_by="video",
            metadata={"telegram_data_post": "example/8454"},
        )
        self.assertFalse(candidate_matches_telegram_source(missing_provenance, source))
        self.assertFalse(candidate_matches_telegram_source(wrong_provenance, source))
        self.assertFalse(candidate_matches_telegram_source(foreign, source))


if __name__ == "__main__":
    unittest.main()
