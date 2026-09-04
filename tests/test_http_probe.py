import unittest

from downloader.http_probe import HTTPProbe


class FakeHeaders(dict):
    pass


class FakeResponse:
    def __init__(self, *, status=200, content_type="video/mp4",
                 content_length="1234", body=b"media"):
        self.status = status
        self.headers = FakeHeaders({
            "Content-Type": content_type,
            "Content-Length": content_length,
        })
        self.body = body
        self.closed = False

    def read(self, size=-1):
        return self.body[:size]

    def close(self):
        self.closed = True


class HTTPProbeTests(unittest.TestCase):
    def setUp(self):
        self.validated = []

        def validator(url):
            self.validated.append(url)

        self.response = FakeResponse()

        def opener(url, **kwargs):
            self.open_args = (url, kwargs)
            return self.response

        self.probe = HTTPProbe(validator, opener)

    def test_collects_basic_http_metadata(self):
        result = self.probe.probe(
            "https://media.example/video.mp4",
            kind="progressive",
        )

        self.assertTrue(result.reachable)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.content_type, "video/mp4")
        self.assertEqual(result.content_length, 1234)
        self.assertEqual(result.bytes_read, 5)
        self.assertIsNone(result.error)
        self.assertEqual(
            self.validated,
            ["https://media.example/video.mp4"],
        )

    def test_passes_timeout_limit_and_kind_to_opener(self):
        self.probe.probe(
            "https://media.example/video.mp4",
            timeout=9,
            max_bytes=4096,
            kind="progressive",
        )

        self.assertEqual(
            self.open_args,
            (
                "https://media.example/video.mp4",
                {
                    "timeout": 9,
                    "max_bytes": 4096,
                    "kind": "progressive",
                },
            ),
        )

    def test_url_validation_failure_is_safe(self):
        def validator(_url):
            raise ValueError("private address")

        opener_called = []

        def opener(*args, **kwargs):
            opener_called.append(True)
            return FakeResponse()

        probe = HTTPProbe(validator, opener)

        result = probe.probe("http://127.0.0.1/video.mp4")

        self.assertFalse(result.reachable)
        self.assertEqual(
            result.error,
            "url_validation_failed:ValueError",
        )
        self.assertEqual(opener_called, [])

    def test_probe_failure_is_structured(self):
        def opener(*args, **kwargs):
            raise TimeoutError("timed out")

        probe = HTTPProbe(lambda _url: None, opener)

        result = probe.probe("https://media.example/video.mp4")

        self.assertFalse(result.reachable)
        self.assertEqual(
            result.error,
            "probe_failed:TimeoutError",
        )

    def test_rejects_oversized_probe_read(self):
        response = FakeResponse(body=b"x" * 20)

        probe = HTTPProbe(
            lambda _url: None,
            lambda *args, **kwargs: response,
        )

        result = probe.probe(
            "https://media.example/video.mp4",
            max_bytes=10,
        )

        self.assertTrue(result.reachable)
        self.assertEqual(result.bytes_read, 10)
        self.assertEqual(
            result.error,
            "probe_response_exceeds_limit",
        )
        self.assertTrue(response.closed)

    def test_invalid_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            self.probe.probe("")

        with self.assertRaises(ValueError):
            self.probe.probe(
                "https://media.example/video.mp4",
                timeout=0,
            )

        with self.assertRaises(ValueError):
            self.probe.probe(
                "https://media.example/video.mp4",
                max_bytes=0,
            )


if __name__ == "__main__":
    unittest.main()
