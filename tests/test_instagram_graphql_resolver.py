from pathlib import Path
import json
import urllib.error

from downloader.instagram_graphql import (
    DEFAULT_DOC_ID,
    _extract_item,
    _parse_source,
    _select_video,
    download_instagram_with_graphql,
)


class FakeResponse:
    def __init__(self, payload, content_type="video/mp4"):
        self._payload = payload
        self.headers = type(
            "Headers",
            (),
            {"get_content_type": lambda self: content_type},
        )()

    def read(self, size=-1):
        payload = self._payload
        self._payload = b""
        return payload

    def close(self):
        pass


def test_parse_only_canonical_instagram_posts():
    assert _parse_source("https://www.instagram.com/reel/ABC_123/") == ("reel", "ABC_123")
    assert _parse_source("https://www.instagram.com/p/ABC-123/") == ("p", "ABC-123")
    assert _parse_source("https://www.instagram.com/accounts/login/") is None
    assert _parse_source("http://www.instagram.com/reel/ABC/") is None
    assert _parse_source("https://example.com/reel/ABC/") is None


def test_graphql_extract_requires_exact_shortcode():
    item = {"code": "ABC123", "video_versions": [{"url": "https://cdninstagram.com/video.mp4"}]}
    data = {"data": {"xdt_api__v1__media__shortcode__web_info": {"items": [item]}}}
    extracted, reason = _extract_item(data, "ABC123")
    assert extracted == item
    assert reason is None

    extracted, reason = _extract_item(data, "WRONG")
    assert extracted is None
    assert reason == "source_identity_mismatch"


def test_select_video_rejects_ambiguous_carousel():
    item = {
        "code": "POST1",
        "carousel_media": [
            {"media_type": 2, "video_versions": [{"url": "https://cdninstagram.com/1.mp4"}]},
            {"media_type": 2, "video_versions": [{"url": "https://cdninstagram.com/2.mp4"}]},
        ],
    }
    url, diagnostics = _select_video(item)
    assert url is None
    assert diagnostics["reason"] == "ambiguous_carousel_videos"


def test_direct_graphql_resolver_downloads_exact_public_video(tmp_path: Path):
    payload = {
        "data": {
            "xdt_api__v1__media__shortcode__web_info": {
                "items": [{
                    "code": "ABC123",
                    "video_versions": [{"url": "https://cdninstagram.com/video.mp4"}],
                }]
            }
        }
    }
    downloaded = b"fake-video"

    def request_factory(url, **kwargs):
        return (url, kwargs)

    def open_function(request, **kwargs):
        url, options = request
        if url.endswith("/graphql/query"):
            return FakeResponse(json.dumps(payload).encode())
        return FakeResponse(downloaded)

    path, diagnostics = download_instagram_with_graphql(
        "https://www.instagram.com/reel/ABC123/",
        tmp_path,
        request_factory=request_factory,
        open_function=open_function,
    )

    assert path is not None
    assert Path(path).read_bytes() == downloaded
    assert diagnostics["status"] == "success"
    assert diagnostics["doc_id"] == DEFAULT_DOC_ID


def test_direct_graphql_resolver_fail_closed_on_http_error(tmp_path: Path):
    def request_factory(url, **kwargs):
        return url

    def open_function(request, **kwargs):
        raise urllib.error.HTTPError(request, 429, "rate limited", {}, None)

    path, diagnostics = download_instagram_with_graphql(
        "https://www.instagram.com/reel/ABC123/",
        tmp_path,
        request_factory=request_factory,
        open_function=open_function,
    )

    assert path is None
    assert diagnostics["status"] == "http_error"
    assert diagnostics["http_status"] == 429
