from downloader.browser_media_resolver import (
    DEFAULT_MAX_NAV_TARGETS,
    DEFAULT_MAX_PAGES,
    DEFAULT_SETTLE_MS,
    DEFAULT_TIMEOUT_MS,
    _browser_budget,
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
    assert pages == 2
    assert targets == 4
    assert clicks == 4


def test_other_hosts_keep_existing_budget():
    assert _browser_budget(
        "https://example.com/video",
        DEFAULT_TIMEOUT_MS,
        DEFAULT_SETTLE_MS,
        DEFAULT_MAX_PAGES,
    ) == (DEFAULT_TIMEOUT_MS, DEFAULT_SETTLE_MS, DEFAULT_MAX_PAGES, 10, 8)
