from downloader import shhaiid4u_resolver as resolver


def test_platform_detection_is_isolated():
    assert resolver.is_platform_url("https://shhaiid4u.net/watch/example")
    assert resolver.is_platform_url("https://www.shhaiid4u.net/watch/example")
    assert not resolver.is_platform_url("https://shahid4u.run/watch/example")
    assert not resolver.is_platform_url("https://youtube.com/watch?v=x")


def test_canonical_page_url_maps_watch_to_episode_only_on_target_platform():
    source = "https://shhaiid4u.net/watch/example-slug?server=1#player"
    assert resolver._canonical_page_url(source) == "https://shhaiid4u.net/episode/example-slug?server=1#player"
    assert resolver._canonical_page_url("https://shahid4u.run/watch/example") == "https://shahid4u.run/watch/example"
    assert resolver._canonical_page_url("https://shhaiid4u.net/episode/example") == "https://shhaiid4u.net/episode/example"


def test_candidate_normalization_filters_ads_and_ranks_media():
    candidates = resolver._normalize([
        "https://ads.doubleclick.net/video.mp4",
        "https://cdn.example/video.m3u8",
        "https://cdn.example/player/embed?id=1",
        "https://cdn.example/direct_stream/1080p.mp4",
    ])
    assert "https://ads.doubleclick.net/video.mp4" not in candidates
    assert candidates[0] == "https://cdn.example/direct_stream/1080p.mp4"
    assert "https://cdn.example/video.m3u8" in candidates


def test_extract_urls_from_html_finds_public_player_and_media_links():
    html = '''<iframe src="https://player.example/embed/abc"></iframe>
    <script>var src="https:\\/\\/cdn.example\\/video.m3u8";</script>'''
    found = resolver._extract_urls_from_text(html)
    assert "https://player.example/embed/abc" in found
    assert "https://cdn.example/video.m3u8" in found


def test_extract_urls_filters_ad_hosts():
    html = '<script src="https://doubleclick.net/ad.mp4"></script>'
    assert resolver._extract_urls_from_text(html) == []


def test_resolve_does_not_touch_other_hosts(monkeypatch):
    called = []
    monkeypatch.setattr(resolver, "_is_ad_host", lambda url: called.append(url) or False)
    assert resolver.resolve("https://shahid4u.run/watch/1", validator=lambda _: None) == []
    assert called == []


def test_resolve_uses_canonical_episode_route_for_watch_url(monkeypatch):
    async def fake_browser(url, *, validator):
        assert url == "https://shhaiid4u.net/episode/1"
        return ["https://cdn.example/direct_stream/720p.mp4"]

    monkeypatch.setattr(resolver, "_browser_discover", fake_browser)
    result = resolver.resolve("https://shhaiid4u.net/watch/1", validator=lambda _: None)
    assert result == ["https://cdn.example/direct_stream/720p.mp4"]


def test_resolve_uses_bounded_platform_browser(monkeypatch):
    seen = []

    async def fake_browser(url, *, validator):
        seen.append(url)
        return ["https://cdn.example/direct_stream/720p.mp4"]

    monkeypatch.setattr(resolver, "_browser_discover", fake_browser)
    result = resolver.resolve("https://shhaiid4u.net/episode/1", validator=lambda _: None)
    assert seen == ["https://shhaiid4u.net/episode/1"]
    assert result == ["https://cdn.example/direct_stream/720p.mp4"]
