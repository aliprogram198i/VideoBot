from downloader.browser_media_resolver import (
    DEFAULT_MAX_NAV_TARGETS,
    DEFAULT_MAX_PAGES,
    DEFAULT_SETTLE_MS,
    DEFAULT_TIMEOUT_MS,
    _browser_budget,
    _extract_script_media_urls,
)


def test_krx18_gets_bounded_browser_budget():
    timeout, settle, pages, targets, clicks = _browser_budget(
        "https://krx18.com/example",
        DEFAULT_TIMEOUT_MS,
        DEFAULT_SETTLE_MS,
        DEFAULT_MAX_PAGES,
    )
    assert timeout == 12_000
    assert settle == 800
    assert pages == 4
    assert targets == 8
    assert clicks == 6


def test_other_hosts_keep_existing_budget():
    assert _browser_budget(
        "https://example.com/video",
        DEFAULT_TIMEOUT_MS,
        DEFAULT_SETTLE_MS,
        DEFAULT_MAX_PAGES,
    ) == (DEFAULT_TIMEOUT_MS, DEFAULT_SETTLE_MS, DEFAULT_MAX_PAGES, 10, 8)


def test_script_media_extraction_is_limited_to_media_urls():
    source = "var ad = 'https://ads.example/banner.jpg'; var file = 'https:\\/\\/cdn.example/video360.mp4?token=abc';"
    assert _extract_script_media_urls(source) == ["https://cdn.example/video360.mp4?token=abc"]
