import unittest

from downloader.smart_extractor import extract_candidates
from downloader.threads_extractor import extract_threads_media


THREADS_URL = "https://www.threads.com/@example/post/abc123"
CDN_URL = "https://scontent.examplecdn.com/v/t50.2886-16/12345_n.mp4?stp=dst-mp4&_nc_cat=1"
SIGNED_CDN_URL = "https://scontent.examplecdn.com/v/t50.2886-16/12345?efg=eyJ2ZW5jb2RlX3RhZyI6InYifQ"
HLS_URL = "https://cdn.example.com/video/master.m3u8?token=abc"


class ThreadsExtractorTests(unittest.TestCase):
    def test_video_url_without_extension_is_discovered(self):
        html = f'<script>"video_url":"{SIGNED_CDN_URL}"</script>'
        candidates = extract_threads_media(html, THREADS_URL)
        self.assertTrue(any(item.url == SIGNED_CDN_URL for item in candidates))
        self.assertTrue(any(item.kind == "progressive" for item in candidates))

    def test_video_versions_context_is_discovered(self):
        html = f'<script>"video_versions":[{{"type":101,"url":"{CDN_URL}"}}]</script>'
        candidates = extract_threads_media(html, THREADS_URL)
        self.assertTrue(any(item.url == CDN_URL for item in candidates))

    def test_manifest_is_discovered(self):
        html = f'<script>"playback_url":"{HLS_URL}"</script>'
        candidates = extract_threads_media(html, THREADS_URL)
        self.assertTrue(any(item.url == HLS_URL and item.kind == "hls" for item in candidates))

    def test_json_ld_content_url_is_discovered(self):
        html = f'<script type="application/ld+json">{{"video":{{"contentUrl":"{SIGNED_CDN_URL}"}}}}</script>'
        candidates = extract_threads_media(html, THREADS_URL)
        self.assertTrue(any(item.url == SIGNED_CDN_URL for item in candidates))

    def test_open_graph_video_is_discovered_regardless_of_attribute_order(self):
        html = f'<meta content="{SIGNED_CDN_URL}" property="og:video">'
        candidates = extract_threads_media(html, THREADS_URL)
        self.assertTrue(any(item.url == SIGNED_CDN_URL for item in candidates))

    def test_generic_extractor_remains_unchanged_for_normal_media(self):
        html = f'<video src="{CDN_URL}"></video>'
        candidates = extract_candidates(html, THREADS_URL)
        self.assertTrue(any(item.url == CDN_URL for item in candidates))

    def test_non_http_urls_are_rejected(self):
        html = '<script>"video_url":"javascript:alert(1)"</script>'
        self.assertEqual(extract_threads_media(html, THREADS_URL), [])

    def test_invalid_max_candidates_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_threads_media('<script></script>', THREADS_URL, max_candidates=0)


if __name__ == "__main__":
    unittest.main()
