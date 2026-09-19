import unittest

from downloader.smart_extractor import MediaCandidate
from downloader.source_identity import (
    CandidateIdentityGate,
    resolve_source_identity,
)


class SourceIdentityGateTests(unittest.TestCase):
    def test_telegram_identity_is_canonical(self):
        identity = resolve_source_identity("https://t.me/channel/123?foo=bar")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.key, ("telegram", "channel/123"))

    def test_instagram_identity_is_canonical(self):
        identity = resolve_source_identity("https://www.instagram.com/reel/ABC_123/?x=1")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.key, ("instagram", "ABC_123"))

    def test_unsupported_platform_preserves_legacy_acceptance(self):
        gate = CandidateIdentityGate("https://example.com/video/1")
        candidate = MediaCandidate(
            url="https://cdn.example.net/video.mp4",
            kind="progressive",
            source_page="https://example.com/video/1",
            discovered_by="video",
        )
        self.assertTrue(gate.accepts(candidate))

    def test_telegram_requires_exact_provenance(self):
        gate = CandidateIdentityGate("https://t.me/channel/123")
        candidate = MediaCandidate(
            url="https://cdn.example.net/video.mp4",
            kind="progressive",
            source_page="https://t.me/channel/123",
            discovered_by="video",
            metadata={"telegram_data_post": "channel/124"},
        )
        self.assertFalse(gate.accepts(candidate))

    def test_telegram_accepts_exact_provenance(self):
        gate = CandidateIdentityGate("https://t.me/channel/123")
        candidate = MediaCandidate(
            url="https://cdn.example.net/video.mp4",
            kind="progressive",
            source_page="https://t.me/channel/123",
            discovered_by="video",
            metadata={"telegram_data_post": "channel/123"},
        )
        self.assertTrue(gate.accepts(candidate))

    def test_iframe_remains_compatible(self):
        gate = CandidateIdentityGate("https://t.me/channel/123")
        candidate = MediaCandidate(
            url="https://player.example.net/embed/abc",
            kind="iframe",
            source_page="https://t.me/channel/123",
            discovered_by="iframe",
        )
        self.assertTrue(gate.accepts(candidate))


if __name__ == "__main__":
    unittest.main()
