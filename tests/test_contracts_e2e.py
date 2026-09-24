"""Deterministic end-to-end regression tests for the smart extraction contracts.

These tests exercise the real extraction engine with fake network I/O, so they
do not depend on Instagram/YouTube/Facebook availability or production secrets.
"""

from __future__ import annotations

import tempfile
import unittest

from downloader.candidate_ranker import CandidateRanker
from downloader.candidate_validator import CandidateValidator
from downloader.embed_resolver import EmbedResolver
from downloader.page_fetcher import FetchedPage, PageFetcher
from downloader.smart_engine import SmartExtractionEngine
from downloader.smart_extractor import MediaCandidate
from downloader.source_identity import CandidateIdentityGate


class SmartExtractionE2ETests(unittest.TestCase):
    def test_html_to_validated_artifact_candidate(self) -> None:
        source = "https://example.test/watch/123"
        media = "https://cdn.example.test/media/123.mp4"

        def fetch(url: str, **_: object) -> FetchedPage:
            return FetchedPage(
                url=url,
                html=f'<html><body><video src="{media}"></video></body></html>',
                content_type="text/html",
                status=200,
            )

        def validate_url(url: str) -> None:
            self.assertTrue(url.startswith("https://"))

        def probe(url: str, **_: object) -> dict[str, object]:
            self.assertEqual(url, media)
            return {
                "status": 200,
                "content_type": "video/mp4",
                "content_length": 4 * 1024 * 1024,
            }

        engine = SmartExtractionEngine(
            EmbedResolver(PageFetcher(fetch), max_pages=1),
            CandidateValidator(validate_url, probe),
            CandidateRanker(),
        )

        result = engine.extract(source, timeout=5, validation_timeout=2)

        self.assertIsNotNone(result.best_media)
        self.assertEqual(result.best_media.candidate.url, media)
        self.assertEqual(result.best_media.content_type, "video/mp4")
        self.assertEqual(result.valid_candidate_count, 1)
        self.assertFalse(any("no_valid_media_candidate" == item for item in result.diagnostics))

    def test_invalid_candidate_is_rejected_before_best_media(self) -> None:
        source = "https://example.test/watch/456"
        media = "https://cdn.example.test/media/456.mp4"

        def fetch(url: str, **_: object) -> FetchedPage:
            return FetchedPage(
                url=url,
                html=f'<video src="{media}"></video>',
                status=200,
            )

        def validate_url(_: str) -> None:
            return None

        def probe(_: str, **__: object) -> dict[str, object]:
            return {"status": 200, "content_type": "text/html", "content_length": 1000}

        engine = SmartExtractionEngine(
            EmbedResolver(PageFetcher(fetch), max_pages=1),
            CandidateValidator(validate_url, probe),
            CandidateRanker(),
        )

        result = engine.extract(source, timeout=5, validation_timeout=2)

        self.assertIsNone(result.best_media)
        self.assertEqual(result.valid_candidate_count, 0)
        self.assertTrue(any("candidate_rejected:content_type_mismatch" in item for item in result.diagnostics))

    def test_source_identity_gate_fails_closed_for_telegram_provenance(self) -> None:
        candidate = MediaCandidate(
            url="https://cdn.example.test/telegram.mp4",
            kind="progressive",
            source_page="https://t.me/examplechannel/8453",
            discovered_by="video",
            metadata={"telegram_data_post": "examplechannel/9999"},
        )
        gate = CandidateIdentityGate("https://t.me/examplechannel/8453")
        class Result:
            def __init__(self, value: MediaCandidate) -> None:
                self.valid = True
                self.candidate = value
        diagnostics: list[str] = []
        self.assertEqual(gate.filter_results([Result(candidate)], diagnostics), [])
        self.assertTrue(any("source_identity_rejected:telegram" in item for item in diagnostics))


if __name__ == "__main__":
    unittest.main()
