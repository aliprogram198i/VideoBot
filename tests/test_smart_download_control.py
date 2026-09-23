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
