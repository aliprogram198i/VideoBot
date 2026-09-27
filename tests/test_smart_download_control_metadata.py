from plugins.smart_download_control import (
    _dimensions,
    _downloadable_quality,
    _estimated_size,
    _format_name,
    _interaction_line,
    _media_type,
    _text,
    _telegram_status,
    _views,
)


def _formats():
    return [
        {
            "url": "https://example.test/video720",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "width": 1280,
            "height": 720,
            "ext": "mp4",
            "filesize": 8 * 1024 * 1024,
        },
        {
            "url": "https://example.test/video1440",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "width": 1440,
            "height": 2560,
            "ext": "mp4",
            "filesize_approx": 20 * 1024 * 1024,
        },
        {
            "url": "https://example.test/audio",
            "vcodec": "none",
            "acodec": "mp4a",
            "width": 9999,
            "height": 9999,
        },
        {
            "vcodec": "avc1",
            "width": 9999,
            "height": 9999,
        },
    ]


def test_link_info_dimensions_are_not_mislabeled_as_progressive_quality():
    assert _dimensions({"width": 1440, "height": 2560}) == "1440×2560"


def test_downloadable_quality_uses_only_video_formats_with_urls():
    assert _downloadable_quality({"formats": _formats()}) == "1440×2560"


def test_metadata_uses_optional_interaction_fields_without_breaking_missing_data():
    assert "❤️ الإعجابات: 41.0K" in _interaction_line({"like_count": 41000})
    assert "💬 التعليقات: 1.2K" in _interaction_line({"comment_count": 1200})
    assert _interaction_line({}) == ""


def test_estimated_size_and_format_follow_selected_best_downloadable_video():
    data = {"formats": _formats()}
    assert _estimated_size(data) == "20.0 MB"
    assert _format_name(data) == "MP4"


def test_telegram_status_is_unknown_when_source_does_not_expose_size():
    assert _telegram_status({"formats": [{"url": "x", "vcodec": "avc1", "width": 720, "height": 1280}]} ) == "غير متاحة"


def test_telegram_status_distinguishes_safe_and_large_estimates():
    safe = {"formats": [{"url": "x", "vcodec": "avc1", "width": 720, "height": 1280, "filesize": 20 * 1024 * 1024}]}
    large = {"formats": [{"url": "x", "vcodec": "avc1", "width": 720, "height": 1280, "filesize": 60 * 1024 * 1024}]}
    assert _telegram_status(safe) == "مناسب للإرسال"
    assert _telegram_status(large) == "قد يحتاج معالجة"


def test_media_type_is_canonicalized_for_the_card():
    assert _media_type({"media_type": "video"}) == "فيديو"
    assert _media_type({"media_type": "image"}) == "صورة"
    assert _media_type({"media_type": "audio"}) == "صوت"


def test_link_card_contains_new_metadata_without_requiring_optional_fields():
    data = {
        "title": "Example",
        "source": "Instagram",
        "duration": 42,
        "uploader": "creator",
        "view_count": 978000,
        "width": 1080,
        "height": 1920,
        "media_type": "video",
        "formats": [],
    }
    text = _text(data)
    assert "🧩 نوع المحتوى: فيديو" in text
    assert "👁 المشاهدات: 978.0K" in text
    assert "💾 الحجم التقريبي: غير متاحة" in text
    assert "📦 الصيغة: غير متاحة" in text
