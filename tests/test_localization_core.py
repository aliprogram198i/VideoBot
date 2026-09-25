from pathlib import Path


def test_shared_localization_covers_all_supported_languages():
    from plugins.localization import LANGUAGES, t

    assert LANGUAGES == ("ar", "en", "tr", "de")
    for lang in LANGUAGES:
        for section, keys in {
            "smart_search": (
                "title", "page", "previous", "more", "new", "cancel",
                "invalid", "searching", "empty", "expired", "invalid_result",
                "cancelled", "selected", "opening", "retry", "retry_failed",
                "new_prompt",
            ),
            "studio": (
                "audio_formats", "image", "compress", "trim15", "trim30",
                "custom_trim", "resize", "preset", "volume", "back", "mute",
                "audio_prompt", "resize_prompt", "preset_prompt", "volume_prompt",
                "custom_prompt", "invalid_trim", "expired", "failed",
                "done_audio", "done_photo", "done_video", "mp3_status",
                "audio_status", "thumb_status", "trim_status", "custom_status",
                "compress_status", "resize_status", "preset_status",
                "volume_status",
            ),
        }.items():
            for key in keys:
                assert t(section, key, lang)


def test_smart_download_control_has_post_label_for_all_languages():
    source = Path("plugins/smart_download_control.py").read_text(encoding="utf-8")
    assert source.count('"post":') == 4
    assert '"post": "📌 تحميل المنشور"' in source
    assert '"post": "📌 Download post"' in source
    assert '"post": "📌 Gönderiyi indir"' in source
    assert '"post": "📌 Beitrag herunterladen"' in source


def test_smart_search_uses_shared_localization_for_navigation():
    source = Path("plugins/smart_search_pro.py").read_text(encoding="utf-8")
    assert 'from plugins.localization import t, language as normalize_language' in source
    assert 't("smart_search", "previous", language)' in source
    assert 't("smart_search", "more", language)' in source
    assert 't("smart_search", "cancel", language)' in source
