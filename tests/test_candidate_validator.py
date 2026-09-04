import unittest

from downloader.candidate_validator import CandidateValidator
from downloader.smart_extractor import MediaCandidate


class CandidateValidatorTests(unittest.TestCase):
    def setUp(self):
        self.candidate = MediaCandidate(
            url="https://cdn.example.com/video.mp4",
            kind="progressive",
            source_page="https://example.com/watch",
            discovered_by="video",
        )

    def test_rejects_url_when_url_validator_fails(self):
        validator = CandidateValidator(
            lambda url: (_ for _ in ()).throw(ValueError("blocked")),
            lambda **kwargs: {},
        )

        result = validator.validate(self.candidate)

        self.assertFalse(result.valid)
        self.assertTrue(result.reason.startswith("url_validation_failed:"))

    def test_rejects_failed_probe(self):
        validator = CandidateValidator(
            lambda url: None,
            lambda url, **kwargs: (_ for _ in ()).throw(OSError("network")),
        )

        result = validator.validate(self.candidate)

        self.assertFalse(result.valid)
        self.assertTrue(result.reason.startswith("probe_failed:"))

    def test_accepts_valid_progressive_video(self):
        calls = []

        def probe(url, *, timeout, kind):
            calls.append((url, timeout, kind))
            return {
                "status": 200,
                "content_type": "video/mp4",
                "content_length": 1024,
            }

        validator = CandidateValidator(lambda url: None, probe)
        result = validator.validate(self.candidate)

        self.assertTrue(result.valid)
        self.assertEqual(result.reason, "validated")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.content_type, "video/mp4")
        self.assertEqual(result.content_length, 1024)
        self.assertEqual(calls[0][0], self.candidate.url)

    def test_rejects_http_error(self):
        validator = CandidateValidator(
            lambda url: None,
            lambda url, **kwargs: {
                "status": 404,
                "content_type": "text/html",
            },
        )

        result = validator.validate(self.candidate)

        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "http_status:404")

    def test_rejects_content_type_mismatch(self):
        validator = CandidateValidator(
            lambda url: None,
            lambda url, **kwargs: {
                "status": 200,
                "content_type": "text/html",
            },
        )

        result = validator.validate(self.candidate)

        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "content_type_mismatch")

    def test_iframe_is_validated_as_embed_page(self):
        candidate = MediaCandidate(
            url="https://player.example.com/embed/abc",
            kind="iframe",
            source_page="https://example.com/watch",
            discovered_by="iframe",
        )

        validator = CandidateValidator(
            lambda url: None,
            lambda url, **kwargs: {
                "status": 200,
                "content_type": "text/html",
            },
        )

        result = validator.validate(candidate)

        self.assertTrue(result.valid)
        self.assertEqual(result.reason, "embed_page")


if __name__ == "__main__":
    unittest.main()
