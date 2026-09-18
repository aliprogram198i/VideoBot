import asyncio
import time
import unittest
from types import SimpleNamespace

import bot


class GeminiTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.original_client = bot.gemini_client
        self.original_timeout = bot.GEMINI_TIMEOUT_SECONDS

    def tearDown(self):
        bot.gemini_client = self.original_client
        bot.GEMINI_TIMEOUT_SECONDS = self.original_timeout

    def test_gemini_timeout_is_bounded(self):
        class SlowModels:
            @staticmethod
            def generate_content(**_kwargs):
                time.sleep(0.2)
                return SimpleNamespace(text="late")

        bot.gemini_client = SimpleNamespace(models=SlowModels())
        bot.GEMINI_TIMEOUT_SECONDS = 0.01

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(bot.gemini_generate("test"))

    def test_gemini_success_within_budget(self):
        class FastModels:
            @staticmethod
            def generate_content(**_kwargs):
                return SimpleNamespace(text="ok")

        bot.gemini_client = SimpleNamespace(models=FastModels())
        bot.GEMINI_TIMEOUT_SECONDS = 1

        result = asyncio.run(bot.gemini_generate("test"))
        self.assertEqual(result, "ok")


if __name__ == "__main__":
    unittest.main()
