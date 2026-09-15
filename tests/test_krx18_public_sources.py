from downloader.krx18_wp_public_sources import extract_server_targets, post_id_from_url


def test_post_id_from_movie_url():
    assert post_id_from_url("https://krx18.com/movies/84170-example-title/") == "84170"


def test_extract_server_targets_only_uses_explicit_server_rows():
    html = '''
    <div class="video-sources">
      <a href="https://playkrx18.site/watch/84170">Server 1</a>
      <a href="https://mov18plus.cloud/watch/84170">Server 2</a>
      <a href="https://example.com/ad.mp4">Advertisement</a>
    </div>
    '''
    result = extract_server_targets(html, "https://krx18.com/movies/84170-example-title/")
    assert set(result) == {
        "https://playkrx18.site/watch/84170",
        "https://mov18plus.cloud/watch/84170",
    }


def test_extract_server_targets_supports_onclick_and_data_player():
    html = '''
    <div>
      <button data-player="https://playkrx18.site/watch/84170">Server 1</button>
      <button onclick="window.open('https://mov18plus.cloud/watch/84170')">Server 2</button>
    </div>
    '''
    result = extract_server_targets(html, "https://krx18.com/movies/84170-example-title/")
    assert set(result) == {
        "https://playkrx18.site/watch/84170",
        "https://mov18plus.cloud/watch/84170",
    }


def test_extract_server_targets_rejects_direct_media_as_first_hop():
    html = '''
    <div>
      <a href="https://cdn.example/video.mp4">Server 1</a>
      <a href="https://playkrx18.site/watch/84170">Server 2</a>
    </div>
    '''
    result = extract_server_targets(html, "https://krx18.com/movies/84170-example-title/")
    assert result == ["https://playkrx18.site/watch/84170"]
