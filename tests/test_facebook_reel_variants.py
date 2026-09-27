from downloader.facebook_reel_variants import facebook_reel_variants


def test_facebook_reel_returns_exact_id_variant():
    assert facebook_reel_variants(
        "https://www.facebook.com/reel/2115871489331970"
    ) == [
        "https://www.facebook.com/facebook/videos/2115871489331970/",
    ]


def test_facebook_reel_accepts_mobile_host():
    assert facebook_reel_variants(
        "https://m.facebook.com/reel/1234567890/"
    ) == [
        "https://www.facebook.com/facebook/videos/1234567890/",
    ]


def test_non_reel_urls_are_not_modified():
    assert facebook_reel_variants(
        "https://www.facebook.com/share/r/1BvGx4dCiQ/"
    ) == []


def test_non_facebook_urls_are_not_modified():
    assert facebook_reel_variants(
        "https://www.instagram.com/reel/1234567890/"
    ) == []
