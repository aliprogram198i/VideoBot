import asyncio
from types import SimpleNamespace

from plugins.multi_url_batch import install


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self


class FakeContext:
    def __init__(self):
        self.user_data = {}


class FakeBotModule:
    TEXTS = {
        "en": {
            "video_type": "video",
            "audio_type": "audio",
            "banned": "banned",
        }
    }

    def get_language(self, user_id):
        return "en"

    def register_user(self, user):
        pass

    def is_banned(self, user_id):
        return False

    def validate_public_http_url(self, url):
        if "invalid" in url:
            raise ValueError("invalid")

    async def handle_message(self, update, context):
        pass

    async def download_media(self, update, context):
        pass


def test_multi_url_keeps_first_url_for_existing_selection_callbacks():
    bot = FakeBotModule()
    install(bot)
    context = FakeContext()
    update = SimpleNamespace(
        message=FakeMessage("https://example.com/one https://example.com/two"),
        effective_user=SimpleNamespace(id=1),
    )

    asyncio.run(bot.handle_message(update, context))

    assert context.user_data["video_urls"] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    # Existing video_menu/audio_menu callbacks validate this key before
    # download_media is reached. It must therefore remain populated.
    assert context.user_data["video_url"] == "https://example.com/one"
