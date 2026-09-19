from downloader.facebook_identity import candidate_matches_facebook_reel, parse_facebook_reel_url
from downloader.facebook_media_resolver import is_facebook_reel_url
from downloader.smart_extractor import MediaCandidate


REEL_URL = "https://www.facebook.com/reel/2115871489331970/"


def test_facebook_reel_identity_is_exact():
    identity = parse_facebook_reel_url(REEL_URL)
    assert identity is not None
    assert identity.reel_id == "2115871489331970"
    assert is_facebook_reel_url(REEL_URL)
    assert not is_facebook_reel_url("https://www.facebook.com/reel/not-a-number/")


def test_facebook_candidate_provenance_must_match_reel_id():
    identity = parse_facebook_reel_url(REEL_URL)
    assert identity is not None
    matching = MediaCandidate(
        url="https://video.xx.fbcdn.net/v/t42.1790-2/test.mp4",
        kind="progressive",
        source_page="https://www.facebook.com/plugins/video.php",
        discovered_by="script",
        metadata={"facebook_reel_id": identity.reel_id},
    )
    unrelated = MediaCandidate(
        url="https://video.xx.fbcdn.net/v/t42.1790-2/other.mp4",
        kind="progressive",
        source_page="https://www.facebook.com/plugins/video.php",
        discovered_by="script",
        metadata={"facebook_reel_id": "9999999999999999"},
    )
    assert candidate_matches_facebook_reel(matching, identity)
    assert not candidate_matches_facebook_reel(unrelated, identity)


class _Response:
    def __init__(self, html, url):
        self.html = html
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_resolver_returns_only_exact_reel_media_candidate():
    from downloader.facebook_media_resolver import resolve

    html = '<video src="https://video.xx.fbcdn.net/exact.mp4?token=abc"></video>'
    calls = []

    def validator(url):
        calls.append(("validate", url))

    def request_factory(url, **kwargs):
        calls.append(("request", url))
        return url

    def open_function(request, **kwargs):
        return _Response(html, request)

    def read_function(response, **kwargs):
        return response.html

    result = resolve(
        REEL_URL,
        validator=validator,
        request_factory=request_factory,
        open_function=open_function,
        read_function=read_function,
        timeout=5,
    )
    assert result
    assert result[0]["url"].startswith("https://video.xx.fbcdn.net/exact.mp4")
    assert result[0]["metadata"]["facebook_reel_id"] == "2115871489331970"
    assert result[0]["metadata"]["facebook_resolver"] == "facebook_media_resolver_v1"
    assert calls[0] == ("validate", REEL_URL)
