import unittest

from downloader.candidate_validator import CandidateValidator
from downloader.production_network import (
    DEFAULT_PROBE_READ_BYTES,
    DEFAULT_PROBE_TIMEOUT,
    SmartNetworkAdapter,
    build_smart_network_adapter,
)
from downloader.smart_extractor import MediaCandidate


class FakeResponse:
    def __init__(
        self,
        *,
        status=200,
        content_type="video/mp4",
        content_length="104857600",
        body=b"probe-data",
    ):
        self.status = status
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": content_length,
        }
        self.body = body
        self.closed = False

    def read(self, size=-1):
        return self.body[:size]

    def close(self):
        self.closed = True


class ProductionNetworkTests(unittest.TestCase):
    def setUp(self):
        self.candidate = MediaCandidate(
            url="https://cdn.example.com/video.mp4",
            kind="progressive",
            source_page="https://example.com/watch",
            discovered_by="video",
        )

    def test_default_probe_limits_are_defined(self):
        self.assertEqual(DEFAULT_PROBE_READ_BYTES, 64 * 1024)
        self.assertEqual(DEFAULT_PROBE_TIMEOUT, 15.0)

    def test_builds_candidate_validator_probe(self):
        calls = []
        response = FakeResponse(
            content_length=str(100 * 1024 * 1024),
            body=b"x" * 128,
        )

        def request_factory(url, *, headers):
            calls.append(("request", url, headers))
            return ("REQUEST", url)

        def open_function(request, *, timeout, max_bytes):
            calls.append(
                ("open", request, timeout, max_bytes)
            )
            return response

        adapter = build_smart_network_adapter(
            url_validator=lambda url: calls.append(
                ("validate", url)
            ),
            request_factory=request_factory,
            open_function=open_function,
            max_declared_bytes=500 * 1024 * 1024,
            headers={
                "User-Agent": "VideoBot/1.0",
            },
        )

        validator = CandidateValidator(
            lambda url: calls.append(("validator", url)),
            adapter.probe,
        )

        result = validator.validate(
            self.candidate,
            timeout=9.0,
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.reason, "validated")
        self.assertEqual(
            result.content_length,
            100 * 1024 * 1024,
        )

        self.assertEqual(
            calls,
            [
                ("validator", self.candidate.url),
                ("validate", self.candidate.url),
                (
                    "request",
                    self.candidate.url,
                    {"User-Agent": "VideoBot/1.0"},
                ),
                (
                    "open",
                    ("REQUEST", self.candidate.url),
                    9.0,
                    500 * 1024 * 1024,
                ),
            ],
        )

        self.assertTrue(response.closed)

    def test_content_type_mismatch_is_rejected(self):
        response = FakeResponse(
            content_type="text/html",
            content_length="1024",
        )

        def request_factory(url, *, headers):
            return ("REQUEST", url)

        def open_function(request, *, timeout, max_bytes):
            return response

        adapter = SmartNetworkAdapter(
            url_validator=lambda url: None,
            request_factory=request_factory,
            open_function=open_function,
            max_declared_bytes=500 * 1024 * 1024,
        )

        validator = CandidateValidator(
            lambda url: None,
            adapter.probe,
        )

        result = validator.validate(self.candidate)

        self.assertFalse(result.valid)
        self.assertEqual(
            result.reason,
            "content_type_mismatch",
        )
        self.assertTrue(response.closed)

    def test_url_validation_is_called_by_http_probe(self):
        calls = []

        def url_validator(url):
            calls.append(url)

        response = FakeResponse()

        adapter = SmartNetworkAdapter(
            url_validator=url_validator,
            request_factory=lambda url, *, headers: (
                "REQUEST",
                url,
            ),
            open_function=lambda request, *, timeout, max_bytes: (
                response
            ),
            max_declared_bytes=500 * 1024 * 1024,
        )

        result = adapter.probe(
            self.candidate.url,
            timeout=7.0,
            kind="progressive",
        )

        self.assertEqual(result["status"], 200)
        self.assertEqual(
            calls,
            [self.candidate.url],
        )
        self.assertTrue(response.closed)

    def test_redirect_and_security_logic_remain_injected(self):
        security_calls = []

        def url_validator(url):
            security_calls.append(("validate", url))

        def request_factory(url, *, headers):
            security_calls.append(
                ("request", url, headers)
            )
            return ("REQUEST", url)

        def open_function(request, *, timeout, max_bytes):
            security_calls.append(
                ("open", request, timeout, max_bytes)
            )
            return FakeResponse()

        adapter = SmartNetworkAdapter(
            url_validator=url_validator,
            request_factory=request_factory,
            open_function=open_function,
            max_declared_bytes=500 * 1024 * 1024,
            headers={
                "Accept": "video/*",
            },
        )

        result = adapter.probe(
            self.candidate.url,
            timeout=5.0,
            kind="progressive",
        )

        self.assertEqual(result["status"], 200)
        self.assertEqual(
            security_calls[0],
            ("validate", self.candidate.url),
        )
        self.assertEqual(
            security_calls[1],
            (
                "request",
                self.candidate.url,
                {"Accept": "video/*"},
            ),
        )

    def test_rejects_invalid_dependencies(self):
        with self.assertRaises(TypeError):
            SmartNetworkAdapter(
                url_validator=None,
                request_factory=lambda *args, **kwargs: None,
                open_function=lambda *args, **kwargs: None,
                max_declared_bytes=1024,
            )

        with self.assertRaises(TypeError):
            SmartNetworkAdapter(
                url_validator=lambda url: None,
                request_factory=None,
                open_function=lambda *args, **kwargs: None,
                max_declared_bytes=1024,
            )

        with self.assertRaises(TypeError):
            SmartNetworkAdapter(
                url_validator=lambda url: None,
                request_factory=lambda *args, **kwargs: None,
                open_function=None,
                max_declared_bytes=1024,
            )

        with self.assertRaises(ValueError):
            SmartNetworkAdapter(
                url_validator=lambda url: None,
                request_factory=lambda *args, **kwargs: None,
                open_function=lambda *args, **kwargs: None,
                max_declared_bytes=0,
            )


if __name__ == "__main__":
    unittest.main()
