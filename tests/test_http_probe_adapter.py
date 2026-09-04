import unittest

from downloader.candidate_validator import CandidateValidator
from downloader.http_probe import HTTPProbe, ProbeResult
from downloader.http_probe_adapter import (
    HTTPProbeAdapter,
    ProductionProbeOpener,
    make_production_probe_adapter,
)
from downloader.smart_extractor import MediaCandidate


class FakeProbe:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def probe(self, url, *, timeout, kind):
        self.calls.append((url, timeout, kind))
        return self.result


class FakeResponse:
    def __init__(
        self,
        *,
        status=200,
        content_type="video/mp4",
        content_length="104857600",
        body=b"x" * 32,
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


class HTTPProbeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.progressive = MediaCandidate(
            url="https://cdn.example.com/video.mp4",
            kind="progressive",
            source_page="https://example.com/watch",
            discovered_by="video",
        )

    def test_adapter_translates_probe_result(self):
        probe = FakeProbe(
            ProbeResult(
                url=self.progressive.url,
                status=200,
                content_type="video/mp4",
                content_length=1024,
                bytes_read=64,
                reachable=True,
                metadata={"kind": "progressive"},
            )
        )

        adapter = HTTPProbeAdapter(probe)
        result = adapter(
            self.progressive.url,
            timeout=12.0,
            kind="progressive",
        )

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["content_type"], "video/mp4")
        self.assertEqual(result["content_length"], 1024)
        self.assertEqual(result["bytes_read"], 64)
        self.assertTrue(result["reachable"])
        self.assertEqual(result["kind"], "progressive")
        self.assertEqual(
            probe.calls,
            [(self.progressive.url, 12.0, "progressive")],
        )

    def test_adapter_preserves_probe_error(self):
        probe = FakeProbe(
            ProbeResult(
                url=self.progressive.url,
                status=None,
                content_type=None,
                content_length=None,
                bytes_read=0,
                reachable=False,
                error="probe_failed:OSError",
            )
        )

        adapter = HTTPProbeAdapter(probe)
        result = adapter(self.progressive.url, kind="progressive")

        self.assertFalse(result["reachable"])
        self.assertEqual(
            result["probe_error"],
            "probe_failed:OSError",
        )

    def test_adapter_works_directly_with_candidate_validator(self):
        probe = FakeProbe(
            ProbeResult(
                url=self.progressive.url,
                status=200,
                content_type="video/mp4",
                content_length=2048,
                bytes_read=64,
                reachable=True,
            )
        )

        validator = CandidateValidator(
            lambda url: None,
            HTTPProbeAdapter(probe),
        )

        result = validator.validate(self.progressive)

        self.assertTrue(result.valid)
        self.assertEqual(result.reason, "validated")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.content_type, "video/mp4")
        self.assertEqual(result.content_length, 2048)

    def test_adapter_preserves_content_type_mismatch(self):
        probe = FakeProbe(
            ProbeResult(
                url=self.progressive.url,
                status=200,
                content_type="text/html",
                content_length=512,
                bytes_read=64,
                reachable=True,
            )
        )

        validator = CandidateValidator(
            lambda url: None,
            HTTPProbeAdapter(probe),
        )

        result = validator.validate(self.progressive)

        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "content_type_mismatch")

    def test_adapter_requires_probe_interface(self):
        with self.assertRaises(TypeError):
            HTTPProbeAdapter(object())

    def test_adapter_requires_probe_result(self):
        probe = FakeProbe({"status": 200})

        adapter = HTTPProbeAdapter(probe)

        with self.assertRaises(TypeError):
            adapter(self.progressive.url, kind="progressive")

    def test_production_opener_builds_request_and_uses_large_declared_limit(self):
        calls = []

        def request_factory(url, *, headers):
            calls.append(("request", url, headers))
            return ("REQUEST", url, headers)

        response = FakeResponse()

        def open_function(request, *, timeout, max_bytes):
            calls.append(
                ("open", request, timeout, max_bytes)
            )
            return response

        opener = ProductionProbeOpener(
            request_factory=request_factory,
            open_function=open_function,
            max_declared_bytes=500 * 1024 * 1024,
            headers={"User-Agent": "VideoBot/1.0"},
        )

        result = opener(
            self.progressive.url,
            timeout=10.0,
            max_bytes=64 * 1024,
            kind="progressive",
        )

        self.assertIs(result, response)
        self.assertEqual(
            calls,
            [
                (
                    "request",
                    self.progressive.url,
                    {"User-Agent": "VideoBot/1.0"},
                ),
                (
                    "open",
                    (
                        "REQUEST",
                        self.progressive.url,
                        {"User-Agent": "VideoBot/1.0"},
                    ),
                    10.0,
                    500 * 1024 * 1024,
                ),
            ],
        )

    def test_production_adapter_validates_large_progressive_media(self):
        captured = {}

        def request_factory(url, *, headers):
            captured["request"] = (url, headers)
            return ("REQUEST", url)

        response = FakeResponse(
            content_type="video/mp4",
            content_length=str(100 * 1024 * 1024),
            body=b"x" * 64,
        )

        def open_function(request, *, timeout, max_bytes):
            captured["open"] = (
                request,
                timeout,
                max_bytes,
            )
            return response

        adapter = make_production_probe_adapter(
            url_validator=lambda url: None,
            request_factory=request_factory,
            open_function=open_function,
            max_declared_bytes=500 * 1024 * 1024,
            headers={"User-Agent": "VideoBot/1.0"},
        )

        validator = CandidateValidator(
            lambda url: None,
            adapter,
        )

        result = validator.validate(self.progressive)

        self.assertTrue(result.valid)
        self.assertEqual(result.reason, "validated")
        self.assertEqual(result.content_length, 100 * 1024 * 1024)
        self.assertEqual(
            captured["open"][2],
            500 * 1024 * 1024,
        )

    def test_production_opener_rejects_invalid_limit(self):
        with self.assertRaises(ValueError):
            ProductionProbeOpener(
                request_factory=lambda *args, **kwargs: None,
                open_function=lambda *args, **kwargs: None,
                max_declared_bytes=0,
            )


if __name__ == "__main__":
    unittest.main()
