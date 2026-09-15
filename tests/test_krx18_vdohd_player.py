from downloader.krx18_vdohd_player import extract_vdohd_media_urls, is_vdohd_url


def test_vdohd_host_scope():
    assert is_vdohd_url("https://vdohd.com/videos/example/")
    assert is_vdohd_url("https://www.vdohd.com/videos/example/")
    assert not is_vdohd_url("https://example.com/videos/example/")


def test_vdohd_jwplayer_file_config_is_extracted():
    html = '''
    <script>
      jwplayer("player").setup({
        sources: [{file: "https:\\/\\/cdn.example.test\\/stream\\/movie.m3u8"}],
        image: "https://cdn.example.test/poster.jpg"
      });
    </script>
    '''
    result = extract_vdohd_media_urls(html, "https://vdohd.com/videos/example/")
    assert result == ["https://cdn.example.test/stream/movie.m3u8"]


def test_vdohd_rejects_unrelated_image_and_ad_urls():
    html = '''
    <script>
      const poster = "https://cdn.example.test/poster.jpg";
      const ad = "https://doubleclick.net/ad.mp4";
      const file = "https://cdn.example.test/media/movie.mp4";
    </script>
    '''
    result = extract_vdohd_media_urls(html, "https://vdohd.com/videos/example/")
    assert result == ["https://cdn.example.test/media/movie.mp4"]
