from types import SimpleNamespace
from urllib.parse import unquote, urlparse

from downloader.smart_media_bridge import _facebook_embed_urls, _facebook_resolve_share_id


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



SHARE_URL = "https://www.facebook.com/share/r/1BvGx4dCiQ/"
RESOLVED_ID = "2561442584302940"


def test_facebook_share_resolves_canonical_id_from_redirect():
    class FakeResponse:
        def __init__(self, final_url):
            self.final_url = final_url

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return self.final_url

        def read(self, _limit):
            return b""

    class FakeBot:
        Request = staticmethod(lambda url, headers: SimpleNamespace(url=url, headers=headers))

        @staticmethod
        def safe_urlopen(request, timeout, max_bytes):
            assert timeout == 12
            assert max_bytes == 512 * 1024
            return FakeResponse(
                "https://www.facebook.com/reel/2561442584302940/"
            )

    assert _facebook_resolve_share_id(FakeBot, SHARE_URL) == RESOLVED_ID


def test_facebook_share_resolved_id_drives_exact_embed_target():
    variants = _facebook_embed_urls(SHARE_URL, resolved_id=RESOLVED_ID)

    assert len(variants) == 2
    assert RESOLVED_ID in variants[1]
