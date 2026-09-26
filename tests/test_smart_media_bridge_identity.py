import unittest

from downloader.smart_media_bridge import _facebook_embed_urls, _protected_social_post


class SmartMediaBridgeIdentityTests(unittest.TestCase):
    def test_telegram_public_post_is_protected(self):
        self.assertTrue(_protected_social_post("https://t.me/syrevarch/8453"))
        self.assertTrue(_protected_social_post("https://t.me/s/channel/123?single"))

    def test_instagram_public_post_is_protected(self):
        self.assertTrue(_protected_social_post("https://www.instagram.com/reel/DdYyj56qw54/?stkn=x"))
        self.assertTrue(_protected_social_post("https://www.instagram.com/p/ABC_123/"))

    def test_facebook_share_r_routes_to_official_embed(self):
        url = "https://www.facebook.com/share/r/1BvGx4dCiQ/"
        variants = _facebook_embed_urls(url)
        self.assertEqual(len(variants), 1)
        self.assertIn("plugins/video.php", variants[0])
        self.assertIn("1BvGx4dCiQ", variants[0])
        self.assertIn("href=https%3A%2F%2Fwww.facebook.com%2Fshare%2Fr%2F1BvGx4dCiQ%2F", variants[0])

    def test_facebook_share_r_uses_resolved_numeric_id_for_canonical_embed(self):
        url = "https://www.facebook.com/share/r/1BvGx4dCiQ/"
        variants = _facebook_embed_urls(url, resolved_id="2561442584302940")
        self.assertEqual(len(variants), 1)
        self.assertIn("href=https%3A%2F%2Fwww.facebook.com%2Fwatch%2F%3Fv%3D2561442584302940", variants[0])
        self.assertNotIn("href=https%3A%2F%2Fwww.facebook.com%2Fshare%2Fr%2F1BvGx4dCiQ%2F", variants[0])

    def test_facebook_reel_keeps_numeric_embed_variants(self):
        variants = _facebook_embed_urls("https://www.facebook.com/reel/1474514414731627/")
        self.assertEqual(len(variants), 2)
        self.assertTrue(all("plugins/video.php" in variant for variant in variants))

    def test_non_social_source_is_not_protected(self):
        self.assertFalse(_protected_social_post("https://example.com/video/123"))

    def test_invalid_protected_host_fails_closed(self):
        self.assertTrue(_protected_social_post("https://t.me/not-a-post"))
        self.assertTrue(_protected_social_post("https://www.instagram.com/not-a-post"))


if __name__ == "__main__":
    unittest.main()
