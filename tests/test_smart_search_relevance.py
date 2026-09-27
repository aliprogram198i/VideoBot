"""Regression tests for deterministic Smart Search relevance and UX."""

from downloader.smart_search import SearchResult
from plugins.smart_search_pro import (
    _dedupe_and_rank,
    _intent_score,
    _results_message,
    _title_score,
    _year_score,
    _button_label,
)


def result(title, channel="", views=0, score=0.0, duration=180, url=None):
    return SearchResult(
        0, title, url or f"https://www.youtube.com/watch?v={abs(hash(title)) % 10**8:08d}",
        channel, duration, views, score,
    )


def test_exact_title_beats_popular_loose_match():
    candidates = [
        result("Funny compilation of football moments", views=100_000_000, score=0),
        result("football", views=1000, score=0),
    ]
    ranked = _dedupe_and_rank("football", candidates)
    assert ranked[0].title == "football"


def test_year_intent_boosts_matching_title():
    intent = {"years": ["2024"], "intents": []}
    matching = result("Song title 2024", views=0)
    other = result("Song title", views=0)
    assert _year_score(intent, matching) > _year_score(intent, other)


def test_intent_uses_whole_tokens_not_substrings():
    intent = {"years": [], "intents": ["official"]}
    official = result("Artist official video")
    unrelated = result("Artist unofficial video")
    assert _intent_score(intent, official) > _intent_score(intent, unrelated)


def test_duplicate_youtube_ids_are_collapsed():
    a = result("Same video", url="https://www.youtube.com/watch?v=AbCdEf123")
    b = result("Same video duplicate", url="https://youtu.be/AbCdEf123")
    ranked = _dedupe_and_rank("Same video", [a, b])
    assert len(ranked) == 1


def test_long_titles_are_static_and_truncated():
    label = _button_label(0, result("This is a very long title that should remain stable"))
    assert "…" in label
    assert len(label) <= 64


def test_results_message_keeps_selection_prompt_and_titles():
    message = _results_message("test query", [result("A"), result("B")], language="en")
    assert "Choose a result" in message
    assert "1. A" in message
    assert "2. B" in message
