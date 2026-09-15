from downloader.browser_media_resolver import _is_krx18_host


def test_krx18_identity_scope():
    assert _is_krx18_host("https://krx18.com/movie/1")
    assert _is_krx18_host("https://player.krx18.com/embed/1")
    assert not _is_krx18_host("https://example.com/movie/1")
