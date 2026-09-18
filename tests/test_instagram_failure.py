from downloader.instagram_failure import (
    classify_instagram_failure,
    instagram_failure_message,
)


def test_audience_restriction_from_instagram_error():
    result = classify_instagram_failure(
        primary_stderr="This content isn't available to everyone.",
    )
    assert result == "audience_restricted"


def test_cobalt_empty_fetch_is_audience_restriction():
    result = classify_instagram_failure(
        cobalt_diagnostics={"error_code": "error.api.fetch.empty"},
    )
    assert result == "audience_restricted"


def test_login_required_is_distinct():
    result = classify_instagram_failure(
        primary_stderr="Login required to access this content",
    )
    assert result == "login_required"


def test_rate_limit_is_distinct():
    result = classify_instagram_failure(
        primary_stderr="rate-limit reached",
    )
    assert result == "rate_limited"


def test_unknown_preserves_generic_failure():
    result = classify_instagram_failure(
        primary_stderr="unexpected extractor failure",
    )
    assert result == "unknown"


def test_arabic_message_is_localized():
    message = instagram_failure_message(
        "ar",
        cobalt_diagnostics={"error_code": "error.api.fetch.empty"},
    )
    assert "جلسة Instagram العامة" in message
