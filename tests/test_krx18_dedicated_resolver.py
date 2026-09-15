from downloader.krx18_resolver import identity_score, is_krx18_url, _media_url


def test_krx18_identity_requires_strong_public_evidence():
    source = "https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"
    assert identity_score(source, "unrelated trailer") < 40
    assert identity_score(source, "84170 femdom deadly thigh squeeze") >= 100


def test_krx18_media_filter_rejects_known_ad_cdn_hosts():
    assert not _media_url("https://galleryn1.vcmdiawe.com/foo.mp4")
    assert not _media_url("https://z6v2p9a8.bkcdn.net/library/foo.mp4")
    assert _media_url("https://player.example.test/media/movie.m3u8")


def test_krx18_host_scope_is_strict():
    assert is_krx18_url("https://krx18.com/movies/84170-example/")
    assert is_krx18_url("https://www.krx18.com/movies/84170-example/")
    assert not is_krx18_url("https://example.com/movies/84170-example/")
