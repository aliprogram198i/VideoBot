from urllib.parse import unquote, urlparse

from downloader.smart_media_bridge import _facebook_embed_urls


REEL_URL = "https://www.facebook.com/reel/2115871489331970/"


def test_facebook_reel_builds_official_embed_variants():
    variants = _facebook_embed_urls(REEL_URL)

    assert len(variants) == 2
    assert all(
        value.startswith("https://www.facebook.com/plugins/video.php?href=")
        for value in variants
    )
    assert REEL_URL in unquote(urlparse(variants[0]).query.split("href=", 1)[1])
    assert "2115871489331970" in variants[1]


def test_facebook_embed_rejects_non_facebook_or_malformed_urls():
    assert _facebook_embed_urls("https://www.youtube.com/watch?v=2115871489331970") == []
    assert _facebook_embed_urls("https://www.facebook.com/reel/not-a-numeric-id/") == []
    assert _facebook_embed_urls("not-a-url") == []
