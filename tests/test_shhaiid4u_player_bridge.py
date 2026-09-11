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


def test_unresolved_template_is_rejected():
    assert not bridge._sanitize_candidate('https://www.youtube.com/embed/${videoId}')
    assert not bridge._sanitize_candidate('https://www.youtube.com/embed/{{videoId}}')


def test_content_pages_are_rejected_as_media_candidates():
    assert not bridge._sanitize_candidate('https://shhaiid4u.net/download/episode-slug')
    assert not bridge._sanitize_candidate('https://shhaiid4u.net/tag/episode-slug')
    assert not bridge._sanitize_candidate('https://shhaiid4u.net/episode/episode-slug')


def test_download_page_remains_a_bounded_navigation_target():
    assert bridge._is_navigation_target('https://shhaiid4u.net/download/episode-slug')
    assert 'https://shhaiid4u.net/download/episode-slug' in bridge._extract_urls(
        'https://shhaiid4u.net/download/episode-slug',
        base_url='https://shhaiid4u.net/episode/test',
    )


def test_real_media_and_player_candidates_are_kept():
    assert bridge._sanitize_candidate('https://cdn.example.test/video.m3u8')
    assert bridge._sanitize_candidate('https://player.example.test/embed?id=abc123')
    assert bridge._sanitize_candidate('https://media.example.test/source?id=abc123')


def test_rank_prioritizes_real_media_over_player_pages():
    ranked = bridge._rank({
        'https://player.example.test/embed?id=abc123',
        'https://cdn.example.test/video.mp4',
        'https://shhaiid4u.net/download/episode-slug',
    })
    assert ranked[0] == 'https://cdn.example.test/video.mp4'
    assert 'https://shhaiid4u.net/download/episode-slug' not in ranked


def test_non_shhaiid4u_host_isolation_is_preserved(monkeypatch):
    monkeypatch.setattr(bridge.base, 'is_platform_url', lambda url: url.startswith('https://shhaiid4u.net/'))
    assert bridge.resolve('https://example.com/watch/1', validator=lambda url: None) == []
