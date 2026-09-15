from downloader.krx18_resolver import extract_urls_from_onclick, is_krx18_url, rank_targets


def test_krx18_host_scope():
    assert is_krx18_url("https://krx18.com/movies/84170-example/")
    assert is_krx18_url("https://www.krx18.com/movies/84170-example/")
    assert not is_krx18_url("https://example.com/movies/84170-example/")


def test_onclick_public_server_url_extraction():
    value = "window.open('https://playkrx18.site/watch/84170', '_blank')"
    assert extract_urls_from_onclick(value) == ["https://playkrx18.site/watch/84170"]


def test_rank_targets_prefers_explicit_server_player_targets():
    rows = [
        {"href": "https://example.com/ad", "text": "Advertisement", "attr": "", "onclick": ""},
        {"href": "https://playkrx18.site/watch/84170", "text": "Server 1", "attr": "server-btn", "onclick": ""},
        {"href": "https://mov18plus.cloud/watch/84170", "text": "Server 2", "attr": "server-btn", "onclick": ""},
    ]
    result = rank_targets(rows, "https://krx18.com/movies/84170-example/", max_targets=2)
    assert result == [
        "https://mov18plus.cloud/watch/84170",
        "https://playkrx18.site/watch/84170",
    ] or set(result) == {
        "https://playkrx18.site/watch/84170",
        "https://mov18plus.cloud/watch/84170",
    }


def test_rank_targets_accepts_data_url_and_onclick():
    rows = [
        {"data_url": "https://playkrx18.site/watch/84170", "text": "Server 1"},
        {"onclick": "location.href='https://mov18plus.cloud/watch/84170'", "text": "Server 2"},
    ]
    result = rank_targets(rows, "https://krx18.com/movies/84170-example/", max_targets=8)
    assert set(result) == {
        "https://playkrx18.site/watch/84170",
        "https://mov18plus.cloud/watch/84170",
    }
