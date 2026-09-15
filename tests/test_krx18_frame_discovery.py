from downloader.browser_media_resolver import _is_krx18_host, _looks_like_krx18_media_url


def test_krx18_host_detection_is_scoped():
    assert _is_krx18_host("https://krx18.com/movie")
    assert _is_krx18_host("https://player.krx18.com/embed")
    assert not _is_krx18_host("https://notkrx18.com/movie")


def test_krx18_media_url_markers_are_conservative():
    assert _looks_like_krx18_media_url("https://cdn.example/video?id=1")
    assert _looks_like_krx18_media_url("https://cdn.example/player/abc")
    assert not _looks_like_krx18_media_url("https://cdn.example/banner.jpg")
