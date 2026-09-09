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
