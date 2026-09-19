import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from downloader.smart_search import SearchResult
from plugins.smart_search_pro import (
    _canonical_result_key,
    _normalize,
    _dedupe_and_rank,
    search_pro,
    _pick_handler,
)


def result(title, url, channel="", duration=120, score=10.0):
    return SearchResult(0, title, url, channel, duration, 0, score)

def result_factory(title, url):
    return SearchResult(0, title, url, "", 120, 0, 10.0)


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

    def test_pick_handoff_failure_is_recoverable_and_preserves_state(self):
        async def run():
            selected = result_factory("Selected", "https://www.youtube.com/watch?v=Pick123")
            query = SimpleNamespace(
                data="smart_pro_pick_0",
                from_user=SimpleNamespace(id=7),
                message=SimpleNamespace(),
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            context = SimpleNamespace(user_data={
                "smart_search_results": [{"url": selected.url, "title": selected.title}],
                "smart_search_query": "Selected",
                "smart_search_results_expires_at": 9999999999.0,
            })
            bot_module = SimpleNamespace(validate_public_http_url=lambda url: None)
            update = SimpleNamespace(callback_query=query, effective_user=query.from_user)
            with patch(
                "plugins.smart_search_pro.show_control_for_url",
                new=AsyncMock(side_effect=RuntimeError("handoff failed")),
            ):
                await _pick_handler(update, context, bot_module)
            return query, context

        query, context = asyncio.run(run())
        self.assertIn("smart_search_results", context.user_data)
        self.assertEqual(query.answer.await_count, 1)
        query.edit_message_text.assert_awaited()
        final_call = query.edit_message_text.await_args_list[-1]
        self.assertIn("إعادة المحاولة", str(final_call.kwargs.get("reply_markup")))
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
