import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from downloader.smart_search import SearchResult
from plugins.smart_search_pro import (
    _canonical_result_key,
    _normalize,
    _dedupe_and_rank,
    search_pro,
)


def result(title, url, channel="", duration=120, score=10.0):
    return SearchResult(0, title, url, channel, duration, 0, score)


class SmartSearchHardeningTests(unittest.TestCase):
    def test_arabic_normalization_handles_digits_and_tatweel(self):
        self.assertEqual(_normalize("أخــي ٢٠٢٤"), "اخي 2024")

    def test_youtube_identity_dedupes_equivalent_urls_not_titles(self):
        first = result(
            "Same title",
            "https://www.youtube.com/watch?v=AbCdEf123",
            "Channel A",
        )
        equivalent = result(
            "Same title",
            "https://youtu.be/AbCdEf123",
            "Channel B",
            score=5.0,
        )
        different_video_same_title = result(
            "Same title",
            "https://www.youtube.com/watch?v=ZyXwVu987",
            "Channel C",
            score=30.0,
        )
        ranked = _dedupe_and_rank("Same title", [first, equivalent, different_video_same_title])
        self.assertEqual(len(ranked), 2)
        self.assertEqual(_canonical_result_key(first), _canonical_result_key(equivalent))

    def test_channel_relevance_is_secondary_to_title_relevance(self):
        exact = result(
            "Ali Song Official",
            "https://www.youtube.com/watch?v=Exact123",
            "Other Channel",
            score=20.0,
        )
        channel_match = result(
            "Unrelated video",
            "https://www.youtube.com/watch?v=Other123",
            "Ali Song Channel",
            score=20.0,
        )
        ranked = _dedupe_and_rank("Ali Song", [channel_match, exact])
        self.assertEqual(ranked[0].url, exact.url)

    def test_search_pro_uses_bounded_second_variant_and_merges_results(self):
        first = [result("Weak result", "https://www.youtube.com/watch?v=First123", score=1.0)]
        second = [result("Exact title", "https://www.youtube.com/watch?v=Second123", score=20.0)]
        async def fake_search(query):
            return first if query == "أغنية" else second
        async def run():
            with patch("plugins.smart_search_pro.base_search", new=AsyncMock(side_effect=fake_search)):
                return await search_pro("أغــنية")
        ranked = asyncio.run(run())
        self.assertEqual(ranked[0].url, second[0].url)

if __name__ == "__main__":
    unittest.main()
