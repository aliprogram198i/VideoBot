from downloader.content_policy import (
    SAFE_REPLACEMENT_URL,
    is_adult_content_url,
    safe_replacement_url,
)


def test_known_adult_source_is_blocked():
    assert is_adult_content_url("https://krx18.com/movies/example") is True


def test_adult_subdomain_is_blocked():
    assert is_adult_content_url("https://www.krx18.com/movies/example") is True


def test_explicit_metadata_signal_is_blocked():
    assert is_adult_content_url(
        "https://example.com/video",
        {"categories": ["Pornography"]},
    ) is True


def test_normal_content_is_not_blocked():
    assert is_adult_content_url(
        "https://youtube.com/shorts/example",
        {"title": "Funny cooking video", "categories": ["Entertainment"]},
    ) is False


def test_replacement_url_is_stable():
    assert safe_replacement_url() == SAFE_REPLACEMENT_URL
