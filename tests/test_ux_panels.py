from plugins.media_studio import _keyboard_trim, studio_keyboard
from plugins.smart_download_control import _keyboard, _more_keyboard
from plugins.localization import t


def _button_data(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_link_panel_primary_actions_are_compact():
    data = _button_data(_keyboard("https://example.com/video", "ar"))
    assert data[:3] == ["video_menu", "audio_menu", "post_download"]
    assert "sdc_more" in data
    assert "ux_favorite_current" not in data


def test_link_panel_secondary_actions_are_hidden_behind_more():
    markup = _keyboard("https://example.com/video", "ar")
    assert any(
        button.callback_data == "sdc_more"
        for row in markup.inline_keyboard
        for button in row
    )


def test_studio_info_uses_separate_message_and_back_callback():
    data = _button_data(studio_keyboard("0123456789abcdef", "ar"))
    assert "studio:info:0123456789abcdef" in data


def test_studio_main_menu_exposes_shared_link_info():
    data = _button_data(studio_keyboard("0123456789abcdef", "ar"))
    assert "studio:info:0123456789abcdef" in data


def test_studio_info_labels_exist_in_all_languages():
    for lang in ("ar", "en", "tr", "de"):
        assert t("studio", "info", lang)
        assert t("studio", "back_to_studio", lang)


def test_studio_main_menu_is_category_first():
    data = _button_data(studio_keyboard("0123456789abcdef", "ar"))
    assert data[:4] == [
        "studio:audio:0123456789abcdef",
        "studio:trim:0123456789abcdef",
        "studio:thumb:0123456789abcdef",
        "studio:compress:0123456789abcdef",
    ]


def test_studio_trim_has_dedicated_submenu():
    data = _button_data(_keyboard_trim("0123456789abcdef", "ar"))
    assert data[:2] == [
        "studio:trim:0123456789abcdef:15",
        "studio:trim:0123456789abcdef:30",
    ]
    assert "studio:trimcustom:0123456789abcdef" in data


def test_studio_labels_exist_in_all_languages():
    for lang in ("ar", "en", "tr", "de"):
        assert t("studio", "audio", lang)
        assert t("studio", "trim", lang)
        assert t("studio", "trim_prompt", lang)


def test_link_panel_is_context_aware_for_media_type():
    video = _button_data(_keyboard("https://example.com/video", "ar", "video"))
    assert video[:3] == ["video_menu", "audio_menu", "post_download"]

    image = _button_data(_keyboard("https://example.com/image.jpg", "ar", "image"))
    assert image[:1] == ["post_download"]
    assert "video_menu" not in image
    assert "audio_menu" not in image

    audio = _button_data(_keyboard("https://example.com/audio.mp3", "ar", "audio"))
    assert audio[:2] == ["audio_menu", "post_download"]
    assert "video_menu" not in audio


def test_link_panel_more_back_label_is_localized():
    markup = _more_keyboard("https://example.com/video", "ar")
    buttons = [button for row in markup.inline_keyboard for button in row]
    back = next(button for button in buttons if button.callback_data == "main_menu")
    assert back.text == "🔙 رجوع"
