from downloader.smart_search import SearchResult
from plugins.smart_search_pro import _button_label, _format_duration, _format_views, _results_message, _title_window


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


def test_title_window_reveals_full_title_across_offsets():
    title = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    windows = {_title_window(title, 8, offset) for offset in range(len(title) + 7)}
    assert title[:8] in windows
    assert title[-8:] in windows
    assert any("IJKL" in window for window in windows)


def test_marquee_button_changes_visible_title_but_keeps_metadata():
    result = _result(title="عنوان طويل جدًا لاختبار الحركة داخل زر البحث")
    first = _button_label(0, result, 0)
    later = _button_label(0, result, 6)
    assert first != later
    for label in (first, later):
        assert "📺 Test Channel" in label
        assert "⏱ 1:02:03" in label
        assert "👁 1.2M" in label
        assert len(label) <= 64


def test_results_message_keeps_result_data_outside_buttons():
    results = [_result(title="One"), _result(title="Two", index=1)]
    message = _results_message("my <query>", results)
    assert "my &lt;query&gt;" in message
    assert "2 نتائج" in message
    assert "One" not in message
    assert "Two" not in message
