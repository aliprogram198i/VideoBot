from downloader.krx18_wp_public_sources import extract_server_targets


def test_extract_server_targets_collects_multiple_explicit_servers():
    html = '''
    <div><a>Server 1</a><a href="https://one.example/player/1">Play</a></div>
    <div><a>Server 2</a><iframe src="https://two.example/embed/2"></iframe></div>
    <div><a>Server 3</a><a data-player="https://three.example/watch/3">Watch</a></div>
    '''
    targets = extract_server_targets(html, "https://krx18.com/movies/123-example/", max_targets=3)
    assert targets == [
        "https://one.example/player/1",
        "https://three.example/watch/3",
        "https://two.example/embed/2",
    ] or set(targets) == {
        "https://one.example/player/1",
        "https://two.example/embed/2",
        "https://three.example/watch/3",
    }
    assert len(targets) == 3


def test_extract_server_targets_does_not_stop_after_first_server():
    html = '''
    <div>Server 1 <a href="https://one.example/player/1">Player</a></div>
    <div>Server 2 <a href="https://two.example/player/2">Player</a></div>
    <div>Server 3 <a href="https://three.example/player/3">Player</a></div>
    '''
    targets = extract_server_targets(html, "https://krx18.com/movies/123-example/", max_targets=3)
    assert len(targets) == 3
    assert set(targets) == {
        "https://one.example/player/1",
        "https://two.example/player/2",
        "https://three.example/player/3",
    }
