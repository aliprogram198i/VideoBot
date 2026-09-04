import unittest

from downloader.production_page_fetcher import ProductionPageFetcher


class _Headers:
    def get(self, name):
        if name == "Content-Type":
            return "text/html; charset=utf-8"
        return None


class _Response:
    status = 200
    headers = _Headers()

    def read(self, size=-1):
        return b'<meta property="og:url" content="https://www.threads.com/@example/post/abc">'

    def geturl(self):
        return "https://www.threads.com/@example/post/abc"

    def close(self):
        pass


class ProductionPageFetcherRedirectTests(unittest.TestCase):
    def test_uses_final_response_url_after_share_redirect(self):
        fetcher = ProductionPageFetcher(
            request_factory=lambda url, headers: (url, headers),
            open_function=lambda request, **kwargs: _Response(),
            read_function=lambda response, max_bytes: response.read(max_bytes),
            max_html_bytes=1024 * 1024,
        )

        page = fetcher(
            "https://www.threads.com/share/_6pSF8qq6/",
            timeout=10,
            max_bytes=1024 * 1024,
        )

        self.assertEqual(
            page.url,
            "https://www.threads.com/@example/post/abc",
        )
        self.assertIn("og:url", page.html)


if __name__ == "__main__":
    unittest.main()
