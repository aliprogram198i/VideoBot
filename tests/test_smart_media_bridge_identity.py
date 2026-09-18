import unittest

from downloader.smart_media_bridge import _protected_social_post


class SmartMediaBridgeIdentityTests(unittest.TestCase):
    def test_telegram_public_post_is_protected(self):
        self.assertTrue(_protected_social_post("https://t.me/syrevarch/8453"))
        self.assertTrue(_protected_social_post("https://t.me/s/channel/123?single"))

    def test_instagram_public_post_is_protected(self):
        self.assertTrue(_protected_social_post("https://www.instagram.com/reel/DdYyj56qw54/?stkn=x"))
        self.assertTrue(_protected_social_post("https://www.instagram.com/p/ABC_123/"))

    def test_non_social_source_is_not_protected(self):
        self.assertFalse(_protected_social_post("https://example.com/video/123"))

    def test_invalid_protected_host_fails_closed(self):
        self.assertTrue(_protected_social_post("https://t.me/not-a-post"))
        self.assertTrue(_protected_social_post("https://www.instagram.com/not-a-post"))


if __name__ == "__main__":
    unittest.main()
