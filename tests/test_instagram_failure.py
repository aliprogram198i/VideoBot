import unittest

from downloader.instagram_failure import (
    classify_instagram_failure,
    instagram_failure_message,
)


class InstagramFailureClassificationTests(unittest.TestCase):
    def test_classifies_audience_restriction(self):
        category = classify_instagram_failure(
            stderr=(
                "ERROR: [Instagram] abc: This content isn't available to everyone: "
                "It can't be seen by certain audiences."
            )
        )
        self.assertEqual(category, "audience_restricted")

    def test_classifies_cobalt_empty_as_access_unavailable(self):
        self.assertEqual(
            classify_instagram_failure(cobalt_code="error.api.fetch.empty"),
            "access_unavailable",
        )

    def test_classification_does_not_mislabel_unrelated_error(self):
        self.assertEqual(
            classify_instagram_failure(stderr="ERROR: temporary network failure"),
            "unknown",
        )

    def test_unknown_keeps_existing_arabic_message(self):
        message = instagram_failure_message("ar", stderr="temporary network failure")
        self.assertIn("غير متاح حالياً", message)
        self.assertIn("تسجيل الدخول", message)

    def test_audience_message_is_specific(self):
        message = instagram_failure_message(
            "ar",
            stderr="This content isn't available to everyone: It can't be seen by certain audiences.",
        )
        self.assertIn("غير متاح", message)
        self.assertIn("تسجيل الدخول", message)


if __name__ == "__main__":
    unittest.main()
