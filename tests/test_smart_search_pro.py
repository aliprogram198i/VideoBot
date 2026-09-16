from downloader.smart_search import SearchResult
from plugins.smart_search_pro import _button_label, _format_duration, _format_views, _results_message


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


def test_button_label_contains_title_and_all_metadata():
    label = _button_label(0, _result())
    assert "1️⃣" in label
    assert "Test Video <safe>" in label
    assert "📺 Test Channel" in label
    assert "⏱ 1:02:03" in label
    assert "👁 1.2M" in label
    assert "\n" in label
    assert len(label) <= 64


def test_button_label_preserves_metadata_when_title_is_long():
    label = _button_label(4, _result(title="عنوان طويل جداً " * 20, channel="قناة طويلة جداً"))
    assert len(label) <= 64
    assert "📺" in label
    assert "⏱ 1:02:03" in label
    assert "👁 1.2M" in label


def test_results_message_keeps_result_data_outside_buttons():
    results = [_result(title="One"), _result(title="Two", index=1)]
    message = _results_message("my <query>", results)
    assert "my &lt;query&gt;" in message
    assert "2 نتائج" in message
    assert "One" not in message
    assert "Two" not in message
