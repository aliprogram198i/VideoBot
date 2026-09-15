import pytest

from downloader.browser_media_resolver import _browser_budget, _is_krx18_host


def test_krx18_budget_is_host_specific_and_bounded():
    timeout, settle, pages, targets, clicks = _browser_budget(
        "https://krx18.com/movies/84170-example/",
        25_000,
        2_500,
        6,
    )
    assert (timeout, settle, pages, targets, clicks) == (12_000, 800, 4, 8, 6)
    assert _is_krx18_host("https://player.krx18.com/embed/1")


def test_krx18_hard_timeout_constant_is_positive():
    from downloader.browser_media_resolver import KRX18_HARD_BUDGET_SECONDS

    assert 0 < KRX18_HARD_BUDGET_SECONDS <= 60
