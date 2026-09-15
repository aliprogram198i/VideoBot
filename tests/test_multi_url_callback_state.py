import asyncio
from types import SimpleNamespace

from plugins.multi_url_batch import install


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []
        self.edits = []
        self.deleted = False

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return self

    async def delete(self, **kwargs):
        self.deleted = True


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=1, username="tester")
        self.message = FakeMessage()

    async def answer(self, *args, **kwargs):
        pass

    async def edit_message_text(self, text, **kwargs):
        self.message.edits.append((text, kwargs))

    async def delete_message(self, *args, **kwargs):
        self.message.deleted = True


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.sent_messages = []
        outer = self

        class Bot:
            async def send_message(self, chat_id, text, **kwargs):
                message = FakeMessage(text)
                outer.sent_messages.append(message)
                return message

        self.bot = Bot()


class FakeBotModule:
    TEXTS = {
        "en": {"video_type": "video", "audio_type": "audio", "banned": "banned"}
    }

    def get_language(self, user_id):
        return "en"

    def register_user(self, user):
        pass

    def is_banned(self, user_id):
        return False

    def validate_public_http_url(self, url):
        pass

    async def handle_message(self, update, context):
        pass

    async def download_media(self, update, context):
        context.user_data.setdefault("original_calls", []).append(
            (update.callback_query.data, context.user_data.get("video_url"))
        )


def make_update(data=None, text="https://example.com/one https://example.com/two"):
    query = FakeQuery(data) if data is not None else None
    return SimpleNamespace(
        message=FakeMessage(text),
        callback_query=query,
        effective_user=SimpleNamespace(id=1, username="tester"),
        effective_chat=SimpleNamespace(id=99),
    )


def test_intermediate_video_menu_does_not_consume_batch_state():
    bot = FakeBotModule()
    install(bot)
    context = FakeContext()

    message_update = make_update(data=None)
    asyncio.run(bot.handle_message(message_update, context))

    assert context.user_data["video_urls"] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert context.user_data["video_url"] == "https://example.com/one"

    menu_update = make_update(data="video_menu")
    asyncio.run(bot.download_media(menu_update, context))

    assert context.user_data["video_urls"] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert context.user_data["video_url"] == "https://example.com/one"
    assert context.user_data["original_calls"] == [
        ("video_menu", "https://example.com/one")
    ]


def test_terminal_quality_choice_consumes_batch_and_processes_all_urls():
    bot = FakeBotModule()
    install(bot)
    context = FakeContext()

    asyncio.run(bot.handle_message(make_update(data=None), context))
    quality_update = make_update(data="video_720")
    asyncio.run(bot.download_media(quality_update, context))

    assert context.user_data.get("original_calls") == [
        ("video_720", "https://example.com/one"),
        ("video_720", "https://example.com/two"),
    ]
    assert "video_url" not in context.user_data
    assert "video_urls" not in context.user_data
