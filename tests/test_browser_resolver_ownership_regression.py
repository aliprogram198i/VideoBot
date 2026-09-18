import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from downloader import browser_media_resolver


class BrowserResolverOwnershipRegressionTests(unittest.TestCase):
    def test_telegram_source_never_enters_generic_browser_resolver(self):
        async def run():
            with patch.object(
                browser_media_resolver,
                "_resolve_async",
                new=AsyncMock(return_value=["https://cdn.example/wrong.mp4"]),
            ) as generic:
                result = await browser_media_resolver.resolve(
                    "https://t.me/channel/100",
                    validator=lambda url: None,
                )
            generic.assert_not_awaited()
            return result

        self.assertEqual(asyncio.run(run()), [])

    def test_instagram_source_never_enters_generic_browser_resolver(self):
        async def run():
            with patch.object(
                browser_media_resolver,
                "_resolve_async",
                new=AsyncMock(return_value=["https://cdn.example/wrong.mp4"]),
            ) as generic:
                result = await browser_media_resolver.resolve(
                    "https://www.instagram.com/reel/ABC123/",
                    validator=lambda url: None,
                )
            generic.assert_not_awaited()
            return result

        self.assertEqual(asyncio.run(run()), [])


if __name__ == "__main__":
    unittest.main()
