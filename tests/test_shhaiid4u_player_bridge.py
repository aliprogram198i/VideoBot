from downloader import shhaiid4u_player_bridge as bridge


def test_extract_urls_finds_media_from_ajax_html():
    text = '<iframe src="https://cdn.example.test/player?id=7"></iframe><source src="https:\\/\\/cdn.example.test\\/video.m3u8">'
    values = bridge._extract_urls(text, base_url='https://shhaiid4u.net/episode/test')
    assert 'https://cdn.example.test/player?id=7' in values
    assert 'https://cdn.example.test/video.m3u8' in values


def test_extract_urls_rejects_ad_hosts():
    text = 'https://doubleclick.net/player?id=1 https://cdn.example.test/video.m3u8'
    values = bridge._extract_urls(text, base_url='https://shhaiid4u.net/episode/test')
    assert 'https://doubleclick.net/player?id=1' not in values
    assert 'https://cdn.example.test/video.m3u8' in values


def test_interesting_response_is_player_scoped():
    assert bridge._is_interesting_response('https://shhaiid4u.net/wp-admin/admin-ajax.php', 'text/html')
    assert bridge._is_interesting_response('https://cdn.example.test/video.m3u8', 'application/vnd.apple.mpegurl')
    assert not bridge._is_interesting_response('https://ads.example.test/banner', 'text/html')


def test_non_shhaiid4u_host_isolation_is_preserved(monkeypatch):
    monkeypatch.setattr(bridge.base, 'is_platform_url', lambda url: url.startswith('https://shhaiid4u.net/'))
    assert bridge.resolve('https://example.com/watch/1', validator=lambda url: None) == []
