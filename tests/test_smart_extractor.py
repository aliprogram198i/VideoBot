import unittest

from downloader.smart_extractor import extract_candidates, extract_telegram_post_candidates


class SmartExtractorTests(unittest.TestCase):
    def test_extracts_video_source_and_hls(self):
        page = """
        <video src="/media/movie.mp4"></video>
        <source src="//cdn.example.com/master.m3u8?token=abc">
        """

        candidates = extract_candidates(
            page,
            "https://example.com/watch/123",
        )

        urls = {candidate.url for candidate in candidates}

        self.assertIn(
            "https://example.com/media/movie.mp4",
            urls,
        )
        self.assertIn(
            "https://cdn.example.com/master.m3u8?token=abc",
            urls,
        )

    def test_discovers_iframe_but_does_not_call_it_media(self):
        page = """
        <iframe src="https://player.example.com/embed/abc"></iframe>
        """

        candidates = extract_candidates(
            page,
            "https://example.com/watch",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, "iframe")
        self.assertEqual(candidates[0].discovered_by, "iframe")

    def test_extracts_escaped_script_url(self):
        page = r"""
        <script>
        const source = "https:\/\/cdn.example.com\/video\/master.m3u8?x=1";
        </script>
        """

        candidates = extract_candidates(
            page,
            "https://example.com/watch",
        )

        self.assertTrue(
            any(
                candidate.url
                == "https://cdn.example.com/video/master.m3u8?x=1"
                for candidate in candidates
            )
        )

    def test_relative_media_url_is_resolved(self):
        page = '<source src="../media/video.webm">'

        candidates = extract_candidates(
            page,
            "https://example.com/a/b/watch",
        )

        self.assertEqual(
            candidates[0].url,
            "https://example.com/a/media/video.webm",
        )

    def test_deduplicates_candidates(self):
        page = """
        <video src="/video/movie.mp4"></video>
        <source src="/video/movie.mp4">
        <script>var x="/video/movie.mp4";</script>
        """

        candidates = extract_candidates(
            page,
            "https://example.com/watch",
        )

        media_urls = [
            candidate.url
            for candidate in candidates
            if candidate.kind == "progressive"
        ]

        self.assertEqual(
            media_urls.count("https://example.com/video/movie.mp4"),
            1,
        )


    def test_telegram_data_post_fallback_extracts_only_exact_message(self):
        page = """
        <div data-post="syrevarch/8452"><video src="https://cdn.example/previous"></video></div>
        <section data-post="syrevarch/8453"><video src="https://cdn.example/target"></video></section>
        <div data-post="syrevarch/8454"><video src="https://cdn.example/next"></video></div>
        """
        candidates = extract_telegram_post_candidates(
            page,
            "https://t.me/syrevarch/8453?embed=1&single=1",
            channel="syrevarch",
            message_id=8453,
        )
        self.assertEqual([item.url for item in candidates], ["https://cdn.example/target"])
        self.assertEqual(candidates[0].metadata["telegram_data_post"], "syrevarch/8453")

    def test_telegram_data_post_fallback_rejects_neighboring_message(self):
        page = """
        <section data-post="syrevarch/8454"><video src="https://cdn.example/next"></video></section>
        """
        self.assertEqual(
            extract_telegram_post_candidates(
                page,
                "https://t.me/syrevarch/8453?embed=1",
                channel="syrevarch",
                message_id=8453,
            ),
            [],
        )

    def test_rejects_non_http_page_url(self):
        with self.assertRaises(ValueError):
            extract_candidates(
                '<video src="https://example.com/video.mp4">',
                "file:///tmp/page.html",
            )


if __name__ == "__main__":
    unittest.main()
