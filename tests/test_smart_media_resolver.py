from types import SimpleNamespace

import downloader.smart_media_resolver as resolver
from downloader.smart_media_resolver import _extract, _looks_media_url


def test_iframe_is_page_candidate_not_media_candidate():
    html = '''
    <iframe src="https://player.example.test/embed/abc"></iframe>
    <video><source src="https://cdn.example.test/movie.mp4" type="video/mp4"></video>
    '''
    media, pages = _extract(html, "https://site.example.test/movie")
    media_urls = {url for url, _ in media}
    assert "https://cdn.example.test/movie.mp4" in media_urls
    assert "https://player.example.test/embed/abc" not in media_urls
    assert "https://player.example.test/embed/abc" in pages


def test_extensionless_file_property_is_media_candidate():
    html = '''
    <script>
      const player = {file: "https://cdn.example.test/video?id=123&token=abc"};
    </script>
    '''
    media, pages = _extract(html, "https://site.example.test/movie")
    media_urls = {url for url, _ in media}
    assert "https://cdn.example.test/video?id=123&token=abc" in media_urls
    assert not pages


def test_jwplayer_style_hls_and_dash_are_discoverable():
    html = '''
    <script>
      player.setup({sources:[
        {file:"https://cdn.example.test/master.m3u8"},
        {file:"https://cdn.example.test/manifest.mpd"}
      ]});
    </script>
    '''
    media, _ = _extract(html, "https://site.example.test/movie")
    urls = {url for url, _ in media}
    assert "https://cdn.example.test/master.m3u8" in urls
    assert "https://cdn.example.test/manifest.mpd" in urls


def test_json_ld_content_url_is_media_and_embed_url_is_page():
    html = '''
    <script type="application/ld+json">
      {"contentUrl":"https://cdn.example.test/video?id=7",
       "embedUrl":"https://player.example.test/embed/7"}
    </script>
    '''
    media, pages = _extract(html, "https://site.example.test/movie")
    assert "https://cdn.example.test/video?id=7" in {url for url, _ in media}
    assert "https://player.example.test/embed/7" in pages


def test_media_extension_detection_is_path_based():
    assert _looks_media_url("https://cdn.example.test/a/movie.mp4?token=1")
    assert _looks_media_url("https://cdn.example.test/a/master.m3u8")
    assert not _looks_media_url("https://cdn.example.test/embed/player")


class _FakeResponse:
    def __init__(self, body, content_type="text/html"):
        self._body = body
        self.headers = SimpleNamespace(get_content_type=lambda: content_type)

    def read(self, limit=None):
        return self._body[:limit] if limit else self._body

    def close(self):
        pass


def test_resolver_follows_player_page_to_hls(monkeypatch):
    pages = {
        "https://site.example/movie": (
            b'<iframe src="https://player.example/embed/123"></iframe>',
            "text/html",
        ),
        "https://player.example/embed/123": (
            b'<script>var player={file:"https://cdn.example/master.m3u8"};</script>',
            "text/html",
        ),
        "https://cdn.example/master.m3u8": (
            b"#EXTM3U\n#EXT-X-VERSION:3\n",
            "application/vnd.apple.mpegurl",
        ),
    }

    monkeypatch.setattr(resolver, "_yt_dlp_sources", lambda page_url, validator: [])

    def validator(url):
        assert url.startswith("https://")

    def request_factory(url, headers=None):
        return url

    def open_function(request, timeout=None, max_bytes=None):
        body, content_type = pages[request]
        return _FakeResponse(body, content_type)

    result = resolver.resolve(
        "https://site.example/movie",
        validator=validator,
        request_factory=request_factory,
        open_function=open_function,
        read_function=lambda response, limit: response.read(limit),
    )

    assert result[0] == "https://cdn.example/master.m3u8"


def test_resolver_keeps_extensionless_ytdlp_media_without_probe(monkeypatch):
    source = "https://cdn.example/stream?id=abc123&token=opaque"
    monkeypatch.setattr(resolver, "_yt_dlp_sources", lambda page_url, validator: [(source, 120)])

    result = resolver.resolve(
        "https://player.example/embed/123",
        validator=lambda url: None,
        request_factory=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected page fetch")),
        open_function=lambda *args, **kwargs: None,
        read_function=lambda *args, **kwargs: b"",
    )

    assert result == [source]
