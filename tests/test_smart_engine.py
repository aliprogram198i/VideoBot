import unittest

from downloader.candidate_ranker import CandidateRanker
from downloader.candidate_validator import CandidateValidator
from downloader.embed_resolver import EmbedResolver
from downloader.page_fetcher import FetchedPage, PageFetcher
from downloader.smart_engine import ExtractionResult, SmartExtractionEngine


class SmartEngineTests(unittest.TestCase):
    def make_engine(self, pages, probes=None):
        probes = probes or {}

        def fetch(url, **kwargs):
            page = pages.get(url)
            if isinstance(page, Exception):
                raise page
            if page is None:
                raise RuntimeError("missing page")
            return FetchedPage(
                url=url,
                html=page,
                content_type="text/html",
                status=200,
            )

        def validate_url(url):
            return None

        def probe(url, **kwargs):
            result = probes.get(url)
            if isinstance(result, Exception):
                raise result
            if result is None:
                return {
                    "status": 200,
                    "content_type": "video/mp4",
                }
            return result

        page_fetcher = PageFetcher(fetch)
        resolver = EmbedResolver(
            page_fetcher,
            max_depth=3,
            max_pages=10,
            max_candidates=100,
        )
        validator = CandidateValidator(validate_url, probe)
        ranker = CandidateRanker()

        return SmartExtractionEngine(
            resolver,
            validator,
            ranker,
        )

    def test_extracts_best_media_from_nested_embed(self):
        root = "https://example.com/watch"
        embed = "https://player.example.com/embed"
        media = "https://cdn.example.com/video.mp4"

        pages = {
            root: f'<iframe src="{embed}"></iframe>',
            embed: f'<video src="{media}"></video>',
        }

        engine = self.make_engine(pages)

        result = engine.extract(root)

        self.assertIsInstance(result, ExtractionResult)
        self.assertIsNotNone(result.best_media)
        self.assertEqual(
            result.best_media.candidate.url,
            media,
        )
        self.assertEqual(
            result.best_media.candidate.kind,
            "progressive",
        )
        self.assertEqual(
            result.visited_pages,
            (root, embed),
        )

    def test_hls_beats_progressive_media(self):
        root = "https://example.com/watch"

        hls = "https://cdn.example.com/master.m3u8"
        mp4 = "https://cdn.example.com/video.mp4"

        pages = {
            root: (
                f'<video src="{mp4}"></video>'
                f'<source src="{hls}">'
            )
        }

        probes = {
            hls: {
                "status": 200,
                "content_type": "application/vnd.apple.mpegurl",
            },
            mp4: {
                "status": 200,
                "content_type": "video/mp4",
            },
        }

        engine = self.make_engine(pages, probes)

        result = engine.extract(root)

        self.assertIsNotNone(result.best_media)
        self.assertEqual(
            result.best_media.candidate.url,
            hls,
        )

    def test_invalid_media_is_not_selected(self):
        root = "https://example.com/watch"

        bad = "https://cdn.example.com/video.mp4"
        good = "https://cdn.example.com/master.m3u8"

        pages = {
            root: (
                f'<video src="{bad}"></video>'
                f'<source src="{good}">'
            )
        }

        probes = {
            bad: {
                "status": 200,
                "content_type": "text/html",
            },
            good: {
                "status": 200,
                "content_type": "application/vnd.apple.mpegurl",
            },
        }

        engine = self.make_engine(pages, probes)

        result = engine.extract(root)

        self.assertIsNotNone(result.best_media)
        self.assertEqual(
            result.best_media.candidate.url,
            good,
        )
        self.assertEqual(result.valid_candidate_count, 1)
        self.assertEqual(result.invalid_candidate_count, 1)

    def test_failed_embed_branch_does_not_abort_good_branch(self):
        root = "https://example.com/watch"
        bad_embed = "https://bad.example.com/embed"
        good_embed = "https://good.example.com/embed"
        media = "https://cdn.example.com/video.mp4"

        pages = {
            root: (
                f'<iframe src="{bad_embed}"></iframe>'
                f'<iframe src="{good_embed}"></iframe>'
            ),
            bad_embed: RuntimeError("network failure"),
            good_embed: f'<video src="{media}"></video>',
        }

        engine = self.make_engine(pages)

        result = engine.extract(root)

        self.assertIsNotNone(result.best_media)
        self.assertEqual(
            result.best_media.candidate.url,
            media,
        )

    def test_iframe_is_not_final_media_candidate(self):
        root = "https://example.com/watch"
        embed = "https://player.example.com/embed"

        pages = {
            root: f'<iframe src="{embed}"></iframe>',
            embed: "<html><body>player unavailable</body></html>",
        }

        engine = self.make_engine(pages)

        result = engine.extract(root)

        self.assertIsNone(result.best_media)
        self.assertTrue(
            any(
                item.candidate.kind == "iframe"
                for item in result.ranked_candidates
            )
        )
        self.assertIn(
            "no_valid_media_candidate",
            result.diagnostics,
        )

    def test_resolution_failure_returns_structured_result(self):
        root = "https://example.com/watch"

        engine = self.make_engine(
            {root: RuntimeError("fetch failed")}
        )

        result = engine.extract(root)

        self.assertIsNone(result.best_media)
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(result.ranked_candidates, ())
        self.assertIn(
            "no_pages_visited",
            result.diagnostics,
        )
        self.assertTrue(
            result.diagnostics[0].startswith("resolution_failed:")
        )

    def test_summary_contains_operational_fields(self):
        root = "https://example.com/watch"
        media = "https://cdn.example.com/video.mp4"

        engine = self.make_engine(
            {root: f'<video src="{media}"></video>'}
        )

        result = engine.extract(root)
        summary = SmartExtractionEngine.summarize(result)

        self.assertEqual(summary["source_url"], root)
        self.assertEqual(summary["best_url"], media)
        self.assertEqual(summary["best_kind"], "progressive")
        self.assertEqual(summary["visited_pages"], 1)
        self.assertEqual(summary["candidate_count"], 1)
        self.assertEqual(summary["valid_candidate_count"], 1)
        self.assertEqual(summary["invalid_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
