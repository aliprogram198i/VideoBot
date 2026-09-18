from pathlib import Path
import json

from downloader.instagram_relay_html import (
    _extract_media_from_html,
    _parse_source,
    download_instagram_with_relay,
)


class FakeResponse:
    def __init__(self, payload, content_type="text/html"):
        self._payload = payload
        self.headers = type(
            "Headers", (), {"get_content_type": lambda self: content_type}
        )()

    def read(self, size=-1):
        payload = self._payload
        self._payload = b""
        return payload

    def close(self):
        pass


def test_parse_canonical_instagram_routes():
    assert _parse_source("https://www.instagram.com/reel/ABC123/") == ("reel", "ABC123")
    assert _parse_source("https://www.instagram.com/reels/ABC123/") == ("reels", "ABC123")
    assert _parse_source("https://www.instagram.com/p/ABC_123/") == ("p", "ABC_123")
    assert _parse_source("http://www.instagram.com/reel/ABC123/") is None
    assert _parse_source("https://example.com/reel/ABC123/") is None


def test_relay_extract_requires_exact_shortcode():
    html = """<html><script>
    window.__relay = {"xdt_api__v1__clips__home__connection_v2": true,
    "media":{"code":"ABC123","video_versions":[
      {"url":"https://cdn.instagram.com/a.mp4","type":101}
    ],"has_audio":true}};
    </script></html>"""
    found = _extract_media_from_html(html, "ABC123")
    assert found is not None
    assert found[0] == ["https://cdn.instagram.com/a.mp4"]

    assert _extract_media_from_html(html, "WRONG") is None


def test_relay_rejects_ambiguous_carousel():
    html = """<script>
    {"ScheduledServerJS":true,"code":"ABC123","carousel_media":[
      {"media_type":2,"video_versions":[{"url":"https://cdn.instagram.com/1.mp4"}]},
      {"media_type":2,"video_versions":[{"url":"https://cdn.instagram.com/2.mp4"}]}
    ]}
    </script>"""
    assert _extract_media_from_html(html, "ABC123") is None


def test_relay_downloads_exact_media(tmp_path: Path):
    html = """<script>
    {"ScheduledServerJS":true,"code":"ABC123","video_versions":[
      {"url":"https://cdn.instagram.com/video.mp4"}
    ]}
    </script>"""
    downloaded = b"fake-video"

    def request_factory(url, **kwargs):
        return (url, kwargs)

    def open_function(request, **kwargs):
        url, options = request
        if "instagram.com/reel/ABC123" in url or "instagram.com/p/ABC123" in url:
            return FakeResponse(html.encode(), "text/html")
        return FakeResponse(downloaded, "video/mp4")

    path, diagnostics = download_instagram_with_relay(
        "https://www.instagram.com/reel/ABC123/",
        tmp_path,
        request_factory=request_factory,
        open_function=open_function,
    )
    assert path is not None
    assert Path(path).read_bytes() == downloaded
    assert diagnostics["status"] == "success"
    assert diagnostics["shortcode"] == "ABC123"
