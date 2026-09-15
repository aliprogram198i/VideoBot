import unittest

from downloader.candidate_ranker import CandidateRanker
from downloader.candidate_validator import ValidationResult
from downloader.smart_extractor import MediaCandidate


class CandidateRelevanceTests(unittest.TestCase):
    def _result(self, url: str, size: int | None, score: int = 90):
        candidate = MediaCandidate(
            url=url,
            kind="progressive",
            source_page="https://example.com/watch",
            discovered_by="script",
            score=score,
        )
        return ValidationResult(
            candidate=candidate,
            valid=True,
            status=200,
            content_type="video/mp4",
            content_length=size,
        )

    def test_obvious_ad_media_loses_to_primary_media(self):
        ranker = CandidateRanker()
        ad = self._result("https://cdn.example.com/ads/preroll.mp4", 20 * 1024 * 1024)
        primary = self._result("https://cdn.example.com/media/movie.mp4", 20 * 1024 * 1024)

        ranked = ranker.rank([ad, primary])

        self.assertEqual(ranked[0].candidate.url, primary.candidate.url)

    def test_substantial_media_beats_tiny_secondary_media(self):
        ranker = CandidateRanker()
        tiny = self._result("https://cdn.example.com/player/clip.mp4", 200 * 1024)
        primary = self._result("https://cdn.example.com/media/movie.mp4", 50 * 1024 * 1024)

        ranked = ranker.rank([tiny, primary])

        self.assertEqual(ranked[0].candidate.url, primary.candidate.url)

    def test_short_legitimate_video_is_not_hard_rejected(self):
        ranker = CandidateRanker()
        short = self._result("https://cdn.example.com/social/video.mp4", 2 * 1024 * 1024)

        ranked = ranker.rank([short])

        self.assertEqual(len(ranked), 1)
        self.assertTrue(ranked[0].valid)

    def test_relevance_does_not_change_duplicate_selection(self):
        ranker = CandidateRanker()
        result = self._result("https://cdn.example.com/media/movie.mp4", 10 * 1024 * 1024)

        ranked = ranker.rank([result, result])

        self.assertEqual(len(ranked), 1)


if __name__ == "__main__":
    unittest.main()
