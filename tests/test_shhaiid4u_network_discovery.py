from downloader import shhaiid4u_network_discovery as discovery


def test_network_discovery_isolated_to_shhaiid4u():
    assert discovery.base.is_platform_url("https://shhaiid4u.net/episode/x")
    assert discovery.resolve("https://shahid4u.run/watch/x", validator=lambda _: None) == []


def test_extract_urls_handles_absolute_escaped_and_relative_player_urls():
    html = '''
    <script>var src="https:\\/\\/cdn.example\\/video.m3u8";</script>
    <div data-player="/embed/player?id=7"></div>
    '''
    found = discovery._extract_urls(html, base_url="https://shhaiid4u.net/episode/example")
    assert "https://cdn.example/video.m3u8" in found
    assert "https://shhaiid4u.net/embed/player?id=7" in found


def test_extract_urls_rejects_ad_hosts():
    html = '<script src="https://doubleclick.net/ad.mp4"></script>'
    assert discovery._extract_urls(html, base_url="https://shhaiid4u.net/episode/example") == []


def test_navigation_score_prioritizes_server_and_player_controls():
    assert discovery._score_navigation("Server 2", "https://shhaiid4u.net/server/2") > 0
    assert discovery._score_navigation("watch", "https://example.com/normal") == 0


def test_resolve_normalizes_watch_route_and_uses_bounded_discovery(monkeypatch):
    seen = []

    async def fake_discover(url, *, validator):
        seen.append(url)
        return ["https://cdn.example/direct_stream/720p.mp4"]

    monkeypatch.setattr(discovery, "_discover", fake_discover)
    result = discovery.resolve("https://shhaiid4u.net/watch/1", validator=lambda _: None)
    assert seen == ["https://shhaiid4u.net/episode/1"]
    assert result == ["https://cdn.example/direct_stream/720p.mp4"]
