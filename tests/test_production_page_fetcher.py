import unittest

from downloader.page_fetcher import FetchedPage, PageFetcher
from downloader.production_page_fetcher import (
    DEFAULT_HTML_CONTENT_TYPES,
    ProductionPageFetcher,
    build_production_page_fetcher,
)


class FakeResponse:
    def __init__(self, body=b"<html>ok</html>", status=200, content_type="text/html"):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.body = body
        self.closed = False

    def close(self):
        self.closed = True


class ProductionPageFetcherTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.response = FakeResponse()

        def request_factory(url, *, headers):
            self.calls.append(("request", url, headers))
            return ("REQUEST", url)

        def open_function(
            request,
            *,
            timeout,
            max_bytes,
            expected_content_types,
        ):
            self.calls.append(
                (
                    "open",
                    request,
                    timeout,
                    max_bytes,
                    expected_content_types,
                )
            )
            return self.response

        def read_function(response, max_bytes):
            self.calls.append(("read", max_bytes))
            return response.body

        self.fetcher = ProductionPageFetcher(
            request_factory=request_factory,
            open_function=open_function,
            read_function=read_function,
            max_html_bytes=1024,
            headers={"User-Agent": "AliBot/1.0"},
        )

    def test_returns_fetched_page(self):
        result = self.fetcher(
            "https://example.com/",
            timeout=9.0,
            max_bytes=4096,
        )

        self.assertIsInstance(result, FetchedPage)
        self.assertEqual(result.url, "https://example.com/")
        self.assertEqual(result.html, "<html>ok</html>")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.content_type, "text/html")
        self.assertTrue(self.response.closed)

    def test_enforces_adapter_max_html_bytes(self):
        self.fetcher(
            "https://example.com/",
            timeout=9.0,
            max_bytes=4096,
        )

        self.assertEqual(self.calls[-1], ("read", 1024))
        self.assertEqual(self.calls[-2][3], 1024)

    def test_passes_html_content_types(self):
        self.fetcher(
            "https://example.com/",
            timeout=9.0,
            max_bytes=1024,
        )

        self.assertEqual(
            self.calls[-2][4],
            set(DEFAULT_HTML_CONTENT_TYPES),
        )

    def test_works_with_page_fetcher(self):
        wrapped = PageFetcher(self.fetcher)

        result = wrapped.fetch(
            "https://example.com/",
            timeout=9.0,
            max_bytes=512,
        )

        self.assertEqual(result.html, "<html>ok</html>")

    def test_closes_response_when_read_fails(self):
        def failing_read(response, max_bytes):
            raise RuntimeError("read failed")

        fetcher = ProductionPageFetcher(
            request_factory=lambda url, *, headers: ("REQUEST", url),
            open_function=lambda request, **kwargs: self.response,
            read_function=failing_read,
            max_html_bytes=1024,
        )

        with self.assertRaises(RuntimeError):
            fetcher(
                "https://example.com/",
                timeout=5.0,
                max_bytes=512,
            )

        self.assertTrue(self.response.closed)

    def test_enforces_total_deadline_between_reads(self):
        class FakeSocket:
            def __init__(self):
                self.timeout = 10.0
                self.set_calls = []

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.timeout = value
                self.set_calls.append(value)

        class SlowResponse:
            def __init__(self):
                self.status = 200
                self.headers = {"Content-Type": "text/html"}
                self.closed = False
                sock = FakeSocket()
                self.sock = sock
                self.fp = type(
                    "FP",
                    (),
                    {
                        "raw": type(
                            "Raw",
                            (),
                            {"_sock": sock},
                        )(),
                    },
                )()

            def read(self, size):
                if not hasattr(self, "reads"):
                    self.reads = 0
                self.reads += 1
                return b"<html>part</html>"

            def close(self):
                self.closed = True

        response = SlowResponse()

        def read_twice(proxy, max_bytes):
            first = proxy.read(64)

            import time as _time
            _time.sleep(0.06)

            second = proxy.read(64)
            return first + second

        fetcher = ProductionPageFetcher(
            request_factory=lambda url, *, headers: ("REQUEST", url),
            open_function=lambda request, **kwargs: response,
            read_function=read_twice,
            max_html_bytes=1024,
        )

        with self.assertRaises(TimeoutError):
            fetcher(
                "https://example.com/",
                timeout=0.05,
                max_bytes=512,
            )

        self.assertTrue(response.closed)
        self.assertGreaterEqual(len(response.sock.set_calls), 1)

    def test_restores_socket_timeout_after_successful_read(self):
        class FakeSocket:
            def __init__(self):
                self.timeout = 9.0
                self.set_calls = []

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.timeout = value
                self.set_calls.append(value)

        class SocketResponse(FakeResponse):
            def __init__(self):
                super().__init__()

                sock = FakeSocket()
                self.sock = sock
                self.fp = type(
                    "FP",
                    (),
                    {
                        "raw": type(
                            "Raw",
                            (),
                            {"_sock": sock},
                        )(),
                    },
                )()

            def read(self, size):
                return self.body

        response = SocketResponse()

        fetcher = ProductionPageFetcher(
            request_factory=lambda url, *, headers: ("REQUEST", url),
            open_function=lambda request, **kwargs: response,
            read_function=lambda proxy, max_bytes: proxy.read(max_bytes),
            max_html_bytes=1024,
        )

        result = fetcher(
            "https://example.com/",
            timeout=5.0,
            max_bytes=512,
        )

        self.assertEqual(result.html, "<html>ok</html>")
        self.assertEqual(response.sock.timeout, 9.0)
        self.assertTrue(response.closed)

    def test_rejects_invalid_arguments(self):
        with self.assertRaises(ValueError):
            self.fetcher("", timeout=5.0, max_bytes=100)

        with self.assertRaises(ValueError):
            self.fetcher("https://example.com/", timeout=0, max_bytes=100)

        with self.assertRaises(ValueError):
            self.fetcher("https://example.com/", timeout=5.0, max_bytes=0)

    def test_rejects_invalid_dependencies(self):
        with self.assertRaises(TypeError):
            ProductionPageFetcher(
                request_factory=None,
                open_function=lambda *a, **k: None,
                read_function=lambda *a: b"",
                max_html_bytes=100,
            )

    def test_builder_returns_production_fetcher(self):
        fetcher = build_production_page_fetcher(
            request_factory=lambda *a, **k: None,
            open_function=lambda *a, **k: None,
            read_function=lambda *a: b"",
            max_html_bytes=100,
        )

        self.assertIsInstance(fetcher, ProductionPageFetcher)


if __name__ == "__main__":
    unittest.main()
