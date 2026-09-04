import unittest

from downloader.candidate_ranker import CandidateRanker
from downloader.candidate_validator import ValidationResult
from downloader.smart_extractor import MediaCandidate


def make_result(
    url,
    kind,
    discovered_by,
    *,
    depth=0,
    score=0,
    valid=True,
    status=200,
    content_type=None,
):
    candidate = MediaCandidate(
        url=url,
        kind=kind,
        source_page="https://example.com/watch",
        discovered_by=discovered_by,
        depth=depth,
        score=score,
    )

    return ValidationResult(
        candidate=candidate,
        valid=valid,
        reason="validated" if valid else "failed",
        status=status,
        content_type=content_type,
    )


class CandidateRankerTests(unittest.TestCase):
    def setUp(self):
        self.ranker = CandidateRanker()

    def test_hls_beats_progressive_when_other_signals_are_equal(self):
        hls = make_result(
            "https://cdn.example.com/video.m3u8",
            "hls",
            "script",
            content_type="application/vnd.apple.mpegurl",
        )
        mp4 = make_result(
            "https://cdn.example.com/video.mp4",
            "progressive",
            "script",
            content_type="video/mp4",
        )

        ranked = self.ranker.rank([mp4, hls])

        self.assertEqual(ranked[0].candidate.kind, "hls")

    def test_invalid_candidate_is_not_selected(self):
        invalid = make_result(
            "https://cdn.example.com/bad.mp4",
            "progressive",
            "video",
            valid=False,
        )
        valid = make_result(
            "https://cdn.example.com/good.mp4",
            "progressive",
            "video",
            content_type="video/mp4",
        )

        best = self.ranker.best([invalid, valid])

        self.assertIsNotNone(best)
        self.assertEqual(best.candidate.url, valid.candidate.url)

    def test_shallower_candidate_beats_deeper_equivalent_candidate(self):
        shallow = make_result(
            "https://cdn.example.com/shallow.mp4",
            "progressive",
            "source",
            depth=0,
            content_type="video/mp4",
        )
        deep = make_result(
            "https://cdn.example.com/deep.mp4",
            "progressive",
            "source",
            depth=2,
            content_type="video/mp4",
        )

        ranked = self.ranker.rank([deep, shallow])

        self.assertEqual(ranked[0].candidate.url, shallow.candidate.url)

    def test_video_element_gets_discovery_bonus(self):
        video = make_result(
            "https://cdn.example.com/video.mp4",
            "progressive",
            "video",
            content_type="video/mp4",
        )
        script = make_result(
            "https://cdn.example.com/script.mp4",
            "progressive",
            "script",
            content_type="video/mp4",
        )

        self.assertGreater(
            self.ranker.score(video),
            self.ranker.score(script),
        )

    def test_duplicate_candidates_are_collapsed(self):
        first = make_result(
            "https://cdn.example.com/video.mp4",
            "progressive",
            "script",
            score=10,
            content_type="video/mp4",
        )
        stronger = make_result(
            "https://cdn.example.com/video.mp4",
            "progressive",
            "video",
            score=20,
            content_type="video/mp4",
        )

        ranked = self.ranker.rank([first, stronger])

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].candidate.discovered_by, "video")

    def test_ranking_is_deterministic(self):
        results = [
            make_result(
                "https://cdn.example.com/b.mp4",
                "progressive",
                "script",
                content_type="video/mp4",
            ),
            make_result(
                "https://cdn.example.com/a.mp4",
                "progressive",
                "script",
                content_type="video/mp4",
            ),
        ]

        ranked = self.ranker.rank(results)

        self.assertEqual(
            [item.candidate.url for item in ranked],
            [
                "https://cdn.example.com/a.mp4",
                "https://cdn.example.com/b.mp4",
            ],
        )


if __name__ == "__main__":
    unittest.main()
