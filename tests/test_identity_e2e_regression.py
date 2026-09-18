import unittest
from types import SimpleNamespace
from unittest.mock import patch

from downloader.candidate_ranker import CandidateRanker
from downloader.candidate_validator import CandidateValidator
from downloader.embed_resolver import EmbedResolver
from downloader.page_fetcher import PageFetcher
from downloader.smart_engine import SmartExtractionEngine
from downloader.smart_extractor import MediaCandidate


class SourceIdentityE2ERegressionTests(unittest.TestCase):
    """Regression matrix for source -> candidate -> validation -> ranking."""

    def make_engine(self, candidates):
        resolver = EmbedResolver(PageFetcher(lambda *args, **kwargs: b""))
        validator = CandidateValidator(
            lambda url: url,
            lambda url, **kwargs: {
                "status": 200,
                "content_type": "video/mp4",
                "content_length": 1024,
            },
        )
        engine = SmartExtractionEngine(
            resolver,
            validator,
            CandidateRanker(),
            telemetry_store=None,
        )
        resolution = SimpleNamespace(
            candidates=candidates,
            visited_pages=("https://example.test/source",),
            resolution_error=None,
        )
        return engine, resolution

    @staticmethod
    def candidate(url, source_page, metadata=None):
        return MediaCandidate(
            url=url,
            kind="progressive",
            source_page=source_page,
            discovered_by="video",
            score=100,
            metadata=metadata or {},
        )

    def test_telegram_exact_post_wins_and_neighbor_is_rejected(self):
        source = "https://t.me/channel/100"
        correct = self.candidate(
            "https://cdn.example/100.mp4",
            source,
            {"telegram_data_post": "channel/100"},
        )
        neighbor = self.candidate(
            "https://cdn.example/101.mp4",
            "https://t.me/channel/101",
            {"telegram_data_post": "channel/101"},
        )
        engine, resolution = self.make_engine([neighbor, correct])

        with patch.object(engine.resolver, "resolve", return_value=resolution):
            result = engine.extract(source)

        self.assertIsNotNone(result.best_media)
        self.assertEqual(result.best_media.candidate.url, correct.url)
        self.assertEqual(result.valid_candidate_count, 1)
        self.assertTrue(any("source_identity_gate:telegram:rejected=1" == item for item in result.diagnostics))

    def test_telegram_candidate_without_exact_provenance_is_rejected(self):
        source = "https://t.me/channel/100"
        ambiguous = self.candidate(
            "https://cdn.example/100.mp4",
            source,
            {},
        )
        engine, resolution = self.make_engine([ambiguous])

        with patch.object(engine.resolver, "resolve", return_value=resolution):
            result = engine.extract(source)

        self.assertIsNone(result.best_media)
        self.assertEqual(result.valid_candidate_count, 0)
        self.assertIn("no_valid_media_candidate", result.diagnostics)

    def test_instagram_wrong_shortcode_is_rejected(self):
        source = "https://www.instagram.com/reel/ABC123/"
        wrong = self.candidate(
            "https://cdn.example/wrong.mp4",
            "https://www.instagram.com/reel/XYZ789/",
        )
        engine, resolution = self.make_engine([wrong])

        with patch.object(engine.resolver, "resolve", return_value=resolution):
            result = engine.extract(source)

        self.assertIsNone(result.best_media)
        self.assertEqual(result.valid_candidate_count, 0)
        self.assertTrue(any("source_identity_gate:instagram:rejected=1" == item for item in result.diagnostics))

    def test_instagram_same_source_without_provenance_is_rejected(self):
        source = "https://www.instagram.com/reel/ABC123/"
        ambiguous = self.candidate(
            "https://cdn.example/possible.mp4",
            source,
        )
        engine, resolution = self.make_engine([ambiguous])

        with patch.object(engine.resolver, "resolve", return_value=resolution):
            result = engine.extract(source)

        self.assertIsNone(result.best_media)
        self.assertEqual(result.valid_candidate_count, 0)
        self.assertTrue(
            any(
                "source_identity_gate:instagram:rejected=1" == item
                for item in result.diagnostics
            )
        )

    def test_instagram_exact_provenance_is_accepted(self):
        source = "https://www.instagram.com/reel/ABC123/"
        exact = self.candidate(
            "https://cdn.example/exact.mp4",
            source,
            {"instagram_shortcode": "ABC123"},
        )
        engine, resolution = self.make_engine([exact])

        with patch.object(engine.resolver, "resolve", return_value=resolution):
            result = engine.extract(source)

        self.assertIsNotNone(result.best_media)
        self.assertEqual(result.best_media.candidate.url, exact.url)
        self.assertEqual(result.valid_candidate_count, 1)


if __name__ == "__main__":
    unittest.main()
