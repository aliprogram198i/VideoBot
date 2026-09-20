import unittest
from types import SimpleNamespace

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

    def test_filter_results_rejects_valid_mismatched_candidate(self):
        gate = CandidateIdentityGate("https://www.facebook.com/reel/2115871489331970/")
        good = MediaCandidate(
            url="https://cdn.example.net/good.mp4",
            kind="progressive",
            source_page="https://www.facebook.com/reel/2115871489331970/",
            discovered_by="video",
        )
        bad = MediaCandidate(
            url="https://cdn.example.net/bad.mp4",
            kind="progressive",
            source_page="https://www.facebook.com/reel/9999999999999999/",
            discovered_by="video",
        )
        results = [
            SimpleNamespace(candidate=good, valid=True),
            SimpleNamespace(candidate=bad, valid=True),
        ]
        diagnostics = []
        filtered = gate.filter_results(results, diagnostics)
        self.assertEqual([item.candidate for item in filtered], [good])
        self.assertTrue(any(item.startswith("source_identity_gate:") for item in diagnostics))

    def test_filter_results_preserves_invalid_validation_results(self):
        gate = CandidateIdentityGate("https://www.facebook.com/reel/2115871489331970/")
        invalid = SimpleNamespace(candidate=object(), valid=False)
        diagnostics = []
        self.assertEqual(gate.filter_results([invalid], diagnostics), [invalid])
        self.assertEqual(diagnostics, [])

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
