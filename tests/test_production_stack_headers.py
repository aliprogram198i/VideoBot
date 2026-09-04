import unittest

from downloader.production_stack import build_production_smart_extraction_stack


class _Headers:
    def get(self, name):
        if name == "Content-Type":
            return "text/html; charset=utf-8"
        return None


class _Response:
    status = 200
    headers = _Headers()

    def read(self, size=-1):
        return b"<html></html>"

    def close(self):
        pass


class ProductionStackHeaderTests(unittest.TestCase):
    def test_page_headers_are_reused_by_candidate_probe(self):
        requests = []

        def request_factory(url, headers):
            requests.append((url, dict(headers)))
            return (url, headers)

        def open_function(request, **kwargs):
            return _Response()

        stack = build_production_smart_extraction_stack(
            url_validator=lambda url: None,
            request_factory=request_factory,
            open_function=open_function,
            read_function=lambda response, max_bytes: response.read(max_bytes),
            page_headers={"User-Agent": "test-browser", "Accept-Language": "en-US"},
        )

        stack.page_fetcher.fetch(
            "https://www.threads.com/@example/post/abc",
            timeout=5,
            max_bytes=1024,
        )
        stack.network.probe(
            "https://cdn.example.com/video.mp4",
            timeout=5,
            kind="progressive",
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0][1], requests[1][1])
        self.assertEqual(requests[1][1]["User-Agent"], "test-browser")


if __name__ == "__main__":
    unittest.main()
