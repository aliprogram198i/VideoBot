import unittest

from downloader.embed_resolver import EmbedResolver
from downloader.page_fetcher import FetchedPage, PageFetcher


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, *, timeout, max_bytes):
        self.calls.append((url, timeout, max_bytes))

        if url not in self.pages:
            raise RuntimeError("missing page")

        return FetchedPage(
            url=url,
            html=self.pages[url],
            content_type="text/html",
            status=200,
        )


class EmbedResolverTests(unittest.TestCase):
    def test_resolves_nested_iframes(self):
        pages = {
            "https://site.example/watch": """
                <iframe src="/player/one"></iframe>
            """,
            "https://site.example/player/one": """
                <iframe src="https://player.example/embed/two"></iframe>
            """,
            "https://player.example/embed/two": """
                <video src="https://cdn.example/video/movie.mp4"></video>
            """,
        }

        fake = FakeFetcher(pages)
        resolver = EmbedResolver(
            PageFetcher(fake),
            max_depth=3,
            max_pages=10,
        )

        result = resolver.resolve("https://site.example/watch")

        urls = [candidate.url for candidate in result.candidates]

        self.assertIn(
            "https://cdn.example/video/movie.mp4",
            urls,
        )

        self.assertEqual(
            result.visited_pages,
            (
                "https://site.example/watch",
                "https://site.example/player/one",
                "https://player.example/embed/two",
            ),
        )

        media = [
            candidate
            for candidate in result.candidates
            if candidate.url == "https://cdn.example/video/movie.mp4"
        ]

        self.assertEqual(len(media), 1)
        self.assertEqual(media[0].kind, "progressive")
        self.assertEqual(media[0].depth, 2)

    def test_prevents_cycles(self):
        pages = {
            "https://site.example/a": """
                <iframe src="https://site.example/b"></iframe>
            """,
            "https://site.example/b": """
                <iframe src="https://site.example/a"></iframe>
            """,
        }

        fake = FakeFetcher(pages)
        resolver = EmbedResolver(
            PageFetcher(fake),
            max_depth=10,
            max_pages=10,
        )

        result = resolver.resolve("https://site.example/a")

        self.assertEqual(
            result.visited_pages,
            (
                "https://site.example/a",
                "https://site.example/b",
            ),
        )

    def test_respects_max_depth(self):
        pages = {
            "https://site.example/a": """
                <iframe src="https://site.example/b"></iframe>
            """,
            "https://site.example/b": """
                <video src="https://cdn.example/video.mp4"></video>
            """,
        }

        fake = FakeFetcher(pages)
        resolver = EmbedResolver(
            PageFetcher(fake),
            max_depth=0,
            max_pages=10,
        )

        result = resolver.resolve("https://site.example/a")

        self.assertEqual(len(result.visited_pages), 1)
        self.assertFalse(
            any(
                candidate.kind == "progressive"
                for candidate in result.candidates
            )
        )

    def test_failed_branch_does_not_abort_other_work(self):
        pages = {
            "https://site.example/root": """
                <iframe src="https://bad.example/player"></iframe>
                <iframe src="https://good.example/player"></iframe>
            """,
            "https://good.example/player": """
                <source src="https://cdn.example/good.mp4">
            """,
        }

        fake = FakeFetcher(pages)
        resolver = EmbedResolver(
            PageFetcher(fake),
            max_depth=2,
            max_pages=10,
        )

        result = resolver.resolve("https://site.example/root")

        self.assertTrue(
            any(
                candidate.url == "https://cdn.example/good.mp4"
                for candidate in result.candidates
            )
        )

    def test_page_fetcher_requires_fetched_page(self):
        def invalid_fetch(url, *, timeout, max_bytes):
            return "not-a-page"

        fetcher = PageFetcher(invalid_fetch)

        with self.assertRaises(TypeError):
            fetcher.fetch("https://example.com")


if __name__ == "__main__":
    unittest.main()
