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


def test_smart_media_center_keeps_primary_actions_focused():
    from plugins.smart_download_control import _keyboard

    video = _callbacks(_keyboard("https://example.com/media", "ar", "video"))
    assert video[:5] == ["video_menu", "audio_menu", "post_download", "sdc_more", "sdc_cancel"]

    audio = _callbacks(_keyboard("https://example.com/media", "ar", "audio"))
    assert audio[:4] == ["audio_menu", "post_download", "sdc_more", "sdc_cancel"]

    image = _callbacks(_keyboard("https://example.com/media", "ar", "image"))
    assert image[:3] == ["post_download", "sdc_more", "sdc_cancel"]


def test_smart_studio_is_available_from_more_options():
    from plugins.smart_download_control import _keyboard, _more_keyboard

    callbacks = _callbacks(_keyboard("https://example.com/media", "ar", "video"))
    assert "sdc_studio" not in callbacks
    more_callbacks = _callbacks(_more_keyboard("https://example.com/media", "ar"))
    assert more_callbacks[0] == "sdc_studio"


def test_smart_media_center_title_is_localized():
    from plugins.smart_download_control import _CARD_TEXTS

    assert _CARD_TEXTS["ar"]["title"] == "🧩 مركز الوسائط الذكي"
    assert _CARD_TEXTS["en"]["title"] == "🧩 Smart Media Center"
    assert _CARD_TEXTS["tr"]["title"] == "🧩 Akıllı Medya Merkezi"
    assert _CARD_TEXTS["de"]["title"] == "🧩 Smart Media Center"


def test_studio_callback_reuses_existing_download_artifact():
    import inspect
    from plugins.smart_download_control import callback

    source = inspect.getsource(callback)
    assert "media_context" in source
    assert "_cached_path" in source
    assert "studio_keyboard" in source
    assert "await bot_module.download_media(proxy_update, context)" not in source


def test_smart_studio_callback_is_registered():
    import inspect
    from plugins.smart_download_control import register_smart_download_control

    source = inspect.getsource(register_smart_download_control)
    assert "sdc_studio" in source


def test_metadata_cache_reuses_fresh_probe_and_collection_data():
    import plugins.smart_download_control as sdc

    class Context:
        user_data = {}

    context = Context()
    metadata = {"title": "cached", "media_type": "video"}
    entries = [{"index": 0, "url": "https://example.com/a", "media_type": "video"}]
    sdc._metadata_cache_put(context, " https://example.com/source ", metadata, entries)

    cached_metadata, cached_entries = sdc._metadata_cache_get(context, "https://example.com/source")
    assert cached_metadata == metadata
    assert cached_entries == entries


def test_metadata_cache_expires_stale_entries(monkeypatch):
    import plugins.smart_download_control as sdc

    class Context:
        user_data = {}

    context = Context()
    sdc._metadata_cache_put(context, "https://example.com/source", {"title": "old"}, [])
    monkeypatch.setattr(sdc, "METADATA_CACHE_TTL_SECONDS", 1)
    monkeypatch.setattr(sdc.time, "monotonic", lambda: 10**9)

    cached_metadata, cached_entries = sdc._metadata_cache_get(context, "https://example.com/source")
    assert cached_metadata is None
    assert cached_entries == []


def test_studio_action_reuses_cached_artifact_instead_of_download_pipeline():
    source = __import__("pathlib").Path(__file__).resolve().parents[1] / "plugins" / "smart_download_control.py"
    text = source.read_text(encoding="utf-8")
    start = text.index('    if data == "sdc_studio":')
    end = text.index('    if data == "sdc_cancel":', start)
    section = text[start:end]
    assert "media_context" in section
    assert "_cached_path" in section
    assert "studio_keyboard" in section
    assert "await bot_module.download_media" not in section
    assert "sdc_studio" in section
