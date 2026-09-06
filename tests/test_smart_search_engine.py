from plugins.smart_search_engine import (
    deduplicate,
    expand_query,
    normalize_text,
    quality_filter,
    rank_results,
)


def _item(video_id, title, *, views=0, duration=180, live=False, channel=""):
    return {
        "id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "channel": channel,
        "duration": duration,
        "view_count": views,
        "is_live": live,
    }


def test_normalize_arabic_and_punctuation():
    assert normalize_text("أغنيةٌ ــ جورج وسوف!") == "اغنيه جورج وسوف"


def test_expand_query_is_bounded_and_deterministic():
    variants = expand_query("محمد رمضان أغنية جديدة")
    assert variants[0] == "محمد رمضان أغنية جديدة"
    assert len(variants) <= 3
    assert variants == expand_query("محمد رمضان أغنية جديدة")


def test_deduplicate_by_video_id():
    first = _item("abc", "Test")
    duplicate = _item("abc", "Test duplicate")
    assert len(deduplicate([first, duplicate])) == 1


def test_quality_filter_removes_live_and_shorts_by_default():
    assert not quality_filter("music", _item("1", "music live", live=True))
    assert not quality_filter("music", _item("2", "music shorts"))
    assert quality_filter("music shorts", _item("3", "music shorts"))


def test_rank_results_prefers_relevance_and_phrase_match():
    weak = _item("weak", "Random video", views=100_000_000)
    strong = _item("strong", "George Wassouf - Khasamtak Ah", views=10_000)
    ranked = rank_results("George Wassouf Khasamtak", [weak, strong])
    assert ranked[0]["id"] == "strong"
    assert ranked[0]["score"] > 0


def test_rank_results_returns_at_most_five():
    items = [_item(str(i), f"song {i}", views=i) for i in range(10)]
    assert len(rank_results("song", items)) == 5
