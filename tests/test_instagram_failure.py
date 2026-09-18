from downloader.instagram_failure import (
    classify_instagram_failure,
    instagram_failure_message,
)


def test_classifies_audience_restriction():
    category = classify_instagram_failure(
        stderr="ERROR: [Instagram] abc: This content isn't available to everyone: It can't be seen by certain audiences."
    )
    assert category == "audience_restricted"


def test_classifies_cobalt_empty_as_access_unavailable():
    category = classify_instagram_failure(
        cobalt_code="error.api.fetch.empty"
    )
    assert category == "access_unavailable"


def test_classification_does_not_mislabel_unrelated_error():
    category = classify_instagram_failure(
        stderr="ERROR: temporary network failure"
    )
    assert category == "unknown"


def test_arabic_message_uses_classified_reason():
    message = instagram_failure_message(
        "ar",
        stderr="This content isn't available to everyone: It can't be seen by certain audiences.",
    )
    assert "غير متاح" in message
    assert "تسجيل الدخول" in message
