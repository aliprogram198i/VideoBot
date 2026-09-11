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


def test_resolve_does_not_touch_other_hosts(monkeypatch):
    called = []
    monkeypatch.setattr(resolver, "_is_ad_host", lambda url: called.append(url) or False)
    assert resolver.resolve("https://shahid4u.run/watch/1", validator=lambda _: None) == []
    assert called == []


def test_resolve_uses_canonical_episode_route_for_watch_url(monkeypatch):
    class FakeBrowser:
        @staticmethod
        def resolve(url, **kwargs):
            assert url == "https://shhaiid4u.net/episode/1"
            assert kwargs["max_candidates"] == resolver.MAX_CANDIDATES
            assert kwargs["max_pages"] == resolver.MAX_PAGES
            return ["https://cdn.example/direct_stream/720p.mp4"]

    monkeypatch.setitem(__import__("sys").modules, "downloader.browser_media_resolver", FakeBrowser)
    result = resolver.resolve("https://shhaiid4u.net/watch/1", validator=lambda _: None)
    assert result == ["https://cdn.example/direct_stream/720p.mp4"]


def test_resolve_uses_bounded_browser_for_platform(monkeypatch):
    class FakeBrowser:
        @staticmethod
        def resolve(url, **kwargs):
            assert url.startswith("https://shhaiid4u.net/")
            assert kwargs["max_candidates"] == resolver.MAX_CANDIDATES
            assert kwargs["max_pages"] == resolver.MAX_PAGES
            return ["https://cdn.example/direct_stream/720p.mp4"]

    monkeypatch.setitem(__import__("sys").modules, "downloader.browser_media_resolver", FakeBrowser)
    result = resolver.resolve("https://shhaiid4u.net/episode/1", validator=lambda _: None)
    assert result == ["https://cdn.example/direct_stream/720p.mp4"]
