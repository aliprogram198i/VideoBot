from downloader.smart_search import SearchResult
from plugins.smart_search_pro import _format_duration, _format_views, _result_card, _results_message


def _result(**overrides):
    values = {
        "index": 0,
        "title": "Test Video <safe>",
        "url": "https://example.com/video",
        "channel": "Test Channel",
        "duration": 3723,
        "views": 1_250_000,
        "score": 10.0,
    }
    values.update(overrides)
    return SearchResult(**values)


def test_format_duration_supports_minutes_and_hours():
    assert _format_duration(754) == "12:34"
    assert _format_duration(3723) == "1:02:03"
    assert _format_duration(None) == ""


def test_format_views_is_compact():
    assert _format_views(999) == "999"
    assert _format_views(12_500) == "12.5K"
    assert _format_views(1_250_000) == "1.2M"
    assert _format_views(None) == ""


def test_result_card_escapes_html_and_contains_metadata():
    card = _result_card(0, _result())
    assert "&lt;safe&gt;" in card
    assert "📺 Test Channel" in card
    assert "⏱ 1:02:03" in card
    assert "👁 1.2M" in card


def test_results_message_contains_query_and_all_result_cards():
    results = [_result(title="One"), _result(title="Two", index=1)]
    message = _results_message("my <query>", results)
    assert "my &lt;query&gt;" in message
    assert "1. <b>One</b>" in message
    assert "2. <b>Two</b>" in message
    assert "2 نتائج مطابقة" in message
