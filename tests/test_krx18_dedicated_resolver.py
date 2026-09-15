from downloader.krx18_resolver import identity_score, is_krx18_url, _media_url
from downloader.krx18_wp_public_sources import extract_server_targets


def test_krx18_identity_requires_strong_public_evidence():
    source = "https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"
    assert identity_score(source, "unrelated trailer") < 40
    assert identity_score(source, "84170 femdom deadly thigh squeeze") >= 100


def test_krx18_media_filter_rejects_known_ad_cdn_hosts():
    assert not _media_url("https://galleryn1.vcmdiawe.com/foo.mp4")
    assert not _media_url("https://z6v2p9a8.bkcdn.net/library/foo.mp4")
    assert _media_url("https://player.example.test/media/movie.m3u8")


def test_krx18_host_scope_is_strict():
    assert is_krx18_url("https://krx18.com/movies/84170-example/")
    assert is_krx18_url("https://www.krx18.com/movies/84170-example/")
    assert not is_krx18_url("https://example.com/movies/84170-example/")


def test_krx18_public_source_extraction_accepts_relative_and_data_targets():
    html = '''
    <section>
      <h3>Video Sources</h3>
      <button data-server="/watch/server-1">Server 1</button>
      <a href="/watch/server-2"><span>Server 2</span></a>
      <div data-player="https://player.example.test/embed/84170">Server 3</div>
      <a href="/ads/file.mp4">Download</a>
    </section>
    '''
    targets = extract_server_targets(html, "https://krx18.com/movies/84170-example/", max_targets=3)
    assert targets == [
        "https://krx18.com/watch/server-1",
        "https://krx18.com/watch/server-2",
        "https://player.example.test/embed/84170",
    ]


def test_krx18_public_source_extraction_does_not_promote_unrelated_media():
    html = '''
    <div>Server 1</div>
    <video src="https://ads.example.test/banner.mp4"></video>
    <script>const media = "https://ads.example.test/banner.mp4";</script>
    '''
    assert extract_server_targets(html, "https://krx18.com/movies/84170-example/") == []
