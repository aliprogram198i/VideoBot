from downloader.krx18_wp_public_sources import extract_server_targets, post_id_from_url


def test_post_id_from_krx18_url():
    assert post_id_from_url('https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/') == '84170'


def test_extract_only_explicit_server_targets():
    html = '''
    <div><span>Video Sources</span>
      <a href="https://playkrx18.site/x">Server 1</a>
      <a href="https://mov18plus.cloud/y">Server 2</a>
      <a href="https://ads.example/">Advertisement</a>
      <a href="https://example.invalid/unrelated-watch">Watch unrelated</a>
    </div>
    '''
    targets = set(extract_server_targets(html, 'https://krx18.com/movies/84170-test/'))
    assert targets == {'https://playkrx18.site/x', 'https://mov18plus.cloud/y'}


def test_plain_url_requires_nearby_server_marker():
    html = '''
    <div>Server 1 https://playkrx18.site/watch/84170</div>
    <div>https://example.invalid/unrelated.mp4</div>
    '''
    assert extract_server_targets(html, 'https://krx18.com/movies/84170-test/') == [
        'https://playkrx18.site/watch/84170'
    ]


def test_nested_markup_server_marker_stays_bounded():
    html = '''
    <div class="sources">
      <span>Server 1</span>
      <span><a href="https://playkrx18.site/watch/84170">Watch</a></span>
      <script>const unrelated = "https://ads.example/bad.mp4";</script>
    </div>
    '''
    assert extract_server_targets(html, 'https://krx18.com/movies/84170-test/') == [
        'https://playkrx18.site/watch/84170'
    ]
