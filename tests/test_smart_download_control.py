from plugins.smart_download_control import _duration, _public_url, _source


def test_public_url_accepts_http_and_https_only():
    assert _public_url("https://example.com/video")
    assert _public_url("http://example.com/video")
    assert not _public_url("ftp://example.com/video")
    assert not _public_url("not-a-url")


def test_source_maps_known_platforms():
    assert _source("https://www.youtube.com/watch?v=x") == "YouTube"
    assert _source("https://youtu.be/x") == "YouTube"
    assert _source("https://www.instagram.com/reel/x") == "Instagram"
    assert _source("https://example.com/video") == "example.com"


def test_duration_is_stable_and_human_readable():
    assert _duration(754) == "12:34"
    assert _duration(3723) == "1:02:03"
    assert _duration(None) == "غير متاحة"

def test_keyboard_exposes_post_download_action():
    from plugins.smart_download_control import _keyboard

    keyboard = _keyboard("https://www.instagram.com/p/example", "ar")
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    post_buttons = [button for button in buttons if button.callback_data == "post_download"]
    assert len(post_buttons) == 1
    assert post_buttons[0].text == "📌 تحميل المنشور"


def _callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_smart_media_center_exposes_studio_for_video_and_audio():
    from plugins.smart_download_control import _keyboard

    for media_type in ("video", "audio"):
        callbacks = _callbacks(_keyboard("https://example.com/media", "ar", media_type))
        assert "sdc_studio" in callbacks


def test_smart_media_center_keeps_primary_actions():
    from plugins.smart_download_control import _keyboard

    callbacks = _callbacks(_keyboard("https://example.com/media", "ar", "video"))
    assert {"video_menu", "audio_menu", "sdc_studio", "post_download", "sdc_more", "sdc_cancel"} <= set(callbacks)


def test_smart_media_center_title_is_localized():
    from plugins.smart_download_control import _CARD_TEXTS

    assert _CARD_TEXTS["ar"]["title"] == "🧩 مركز الوسائط الذكي"
    assert _CARD_TEXTS["en"]["title"] == "🧩 Smart Media Center"
    assert _CARD_TEXTS["tr"]["title"] == "🧩 Akıllı Medya Merkezi"
    assert _CARD_TEXTS["de"]["title"] == "🧩 Smart Media Center"


def test_studio_callback_routes_through_existing_download_pipeline():
    import inspect
    from plugins.smart_download_control import callback

    source = inspect.getsource(callback)
    assert 'studio_choice = "audio_best" if media_type == "audio" else "video_best"' in source
    assert "_UpdateProxy(update, proxy_query)" in source
