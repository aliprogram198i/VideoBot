from downloader.krx18_resolver import rank_targets


def test_only_explicit_server_targets_are_selected():
    rows = [
        {"href": "https://playkrx18.site/watch/84170", "text": "Server 1", "attr": "server-btn"},
        {"href": "https://mov18plus.cloud/watch/84170", "text": "Server 2", "attr": "server-btn"},
        {"href": "https://example.com/download", "text": "Download", "attr": "download"},
        {"href": "https://cdn.jsdelivr.net/npm/x.js", "text": "", "attr": ""},
        {"href": "https://bid.onclckbn.net/banner/in/show/", "text": "Server", "attr": ""},
    ]
    result = rank_targets(rows, "https://krx18.com/movies/84170-example/", max_targets=8)
    assert result == [
        "https://mov18plus.cloud/watch/84170",
        "https://playkrx18.site/watch/84170",
    ]


def test_onclick_server_target_is_supported():
    rows = [
        {"onclick": "window.open('https://helvid.net/watch/84170', '_blank')", "text": "Server 3"},
    ]
    assert rank_targets(rows, "https://krx18.com/movies/84170-example/") == [
        "https://helvid.net/watch/84170"
    ]
