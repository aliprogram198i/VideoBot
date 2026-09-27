import asyncio

import pytest
from telegram.ext import ApplicationHandlerStop

from downloader.smart_search import SearchResult
from plugins.smart_search_pro import (
    PAGE_RE,
    PAGE_SIZE,
    _button_label,
    _format_duration,
    _format_views,
    _parse_query_intent,
    _query_variants,
    _results_keyboard,
    _results_message,
    _results_from_state,
)


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


def test_long_title_is_stable_and_truncated_for_comparison():
    result = _result(title="عنوان طويل جدًا لاختبار ثبات الزر وسهولة المقارنة")
    first = _button_label(0, result, 0)
    later = _button_label(0, result, 6)
    assert first == later
    assert "…" in first
    assert "📺 Test Channel" in first
    assert "⏱ 1:02:03" in first
    assert "👁 1.2M" in first
    assert len(first) <= 64


def test_search_timeout_contract_is_bounded_and_kills_child():
    source = Path(__file__).resolve().parents[1] / "downloader" / "smart_search.py"
    text = source.read_text(encoding="utf-8")
    assert "SEARCH_COUNT = 10" in text
    assert "TIMEOUT_SECONDS = 12" in text
    assert "subprocess.Popen(" in text
    assert "process.kill()" in text
    assert "subprocess.TimeoutExpired" in text


def test_entry_url_ignores_non_url_webpage_basename():
    from downloader.smart_search import _entry_url

    entry = {
        "webpage_url_basename": "watch",
        "url": "VIDEO_ID_123456",
        "id": "VIDEO_ID_123456",
    }
    assert _entry_url(entry) == "https://www.youtube.com/watch?v=VIDEO_ID_123456"


def test_entry_url_prefers_explicit_webpage_url():
    from downloader.smart_search import _entry_url

    entry = {
        "webpage_url": "https://www.youtube.com/watch?v=EXPLICIT123",
        "webpage_url_basename": "watch",
        "id": "FALLBACK123",
    }
    assert _entry_url(entry) == "https://www.youtube.com/watch?v=EXPLICIT123"


def test_results_state_round_trips_dict_metadata():
    stored = [{
        "url": "https://youtube.com/watch?v=abc123456",
        "title": "Stored Result",
        "channel": "Channel",
        "duration": 120,
        "views": 5000,
        "score": 12.5,
    }]
    results = _results_from_state(stored)
    assert len(results) == 1
    assert results[0].title == "Stored Result"
    assert results[0].channel == "Channel"
    assert results[0].duration == 120
    assert results[0].views == 5000


def test_page_render_shows_only_visible_results_and_page_indicator():
    results = [_result(title=f"Result {i}", index=i) for i in range(12)]
    message = _results_message("test query", results, page=1)
    assert "النتائج 6–10 من 12" in message
    assert "الصفحة 2/3" in message
    assert "6. Result 5" in message
    assert "10. Result 9" in message
    assert "1. Result 0" not in message
    assert "11. Result 10" not in message


def test_results_message_keeps_page_context_and_visible_titles():
    results = [
        _result(title="First full result title"),
        _result(title="Second full result title", index=1, channel="Other Channel", duration=12, views=42),
        _result(title="Third <full> result title", index=2),
    ]
    message = _results_message("ignored query", results, language="en")
    assert "Choose a result:" in message
    assert message.index("1. First full result title") < message.index("2. Second full result title")
    assert message.index("2. Second full result title") < message.index("3. Third &lt;full&gt; result title")
    assert "Other Channel" not in message
    assert "0:12" not in message
    assert "42" not in message
    assert "ignored query" in message


def test_smart_search_stops_when_media_studio_owns_pending_text():
    from plugins.smart_search_pro import _search_handler

    class FakeMessage:
        text = "00:10 - 00:40"

    class FakeUser:
        id = 123

    class FakeUpdate:
        message = FakeMessage()
        effective_user = FakeUser()

    class FakeContext:
        user_data = {
            "media_studio_pending": {
                "token": "abc123",
                "action": "trimcustom",
            }
        }

    class FakeBotModule:
        def register_user(self, user):
            raise AssertionError("Smart Search must not register or process Media Studio input")

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(_search_handler(FakeUpdate(), FakeContext(), FakeBotModule()))


def test_query_intent_parses_common_search_modifiers_without_ai():
    intent = _parse_query_intent("محمد عبده حفلة live 2024")
    assert "live" in intent["intents"]
    assert intent["years"] == ["2024"]


def test_query_variants_adds_year_free_recall_variant():
    variants = _query_variants("محمد عبده 2024")
    assert variants[0] == "محمد عبده 2024"
    assert "محمد عبده" in variants


def test_page_callback_regex_matches_numeric_pages():
    from plugins.smart_search_pro import PAGE_RE

    assert PAGE_RE.fullmatch("smart_pro_page_0")
    assert PAGE_RE.fullmatch("smart_pro_page_12")
    assert not PAGE_RE.fullmatch("smart_pro_page_x")


def test_results_keyboard_paginates_five_results_per_page():
    results = [_result(title=f"Result {i}", index=i) for i in range(12)]
    first = _results_keyboard(results, page=0)
    second = _results_keyboard(results, page=1)
    third = _results_keyboard(results, page=2)
    assert len(first.inline_keyboard) == PAGE_SIZE + 2
    assert len(second.inline_keyboard) == PAGE_SIZE + 2
    assert len(third.inline_keyboard) == 4
    assert first.inline_keyboard[-2][0].callback_data == "smart_pro_page_1"
    assert second.inline_keyboard[-2][0].callback_data == "smart_pro_page_0"
    assert second.inline_keyboard[-2][1].callback_data == "smart_pro_page_2"
