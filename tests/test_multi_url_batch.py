import asyncio
from types import SimpleNamespace

from plugins.multi_url_batch import extract_urls, install


def run(coro):
    return asyncio.run(coro)


def make_bot_module(download_impl=None, message_impl=None):
    class BotModule:
        TEXTS = {
            "ar": {
                "video_type": "video",
                "audio_type": "audio",
                "banned": "banned",
            },
            "en": {
                "video_type": "video",
                "audio_type": "audio",
                "banned": "banned",
            },
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
            if message_impl:
                return await message_impl(update, context)

        async def download_media(self, update, context):
            if download_impl:
                return await download_impl(update, context)

    return BotModule()


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.edits = []
        self.deleted = False
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return FakeMessage(text)

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return self

    async def delete(self, **kwargs):
        self.deleted = True


class FakeQuery:
    def __init__(self, data="video_720"):
        self.data = data
        self.from_user = SimpleNamespace(id=1, username="tester")
        self.message = FakeMessage()
        self.answers = 0
        self.edits = []
        self.deleted = 0

    async def answer(self, *args, **kwargs):
        self.answers += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def delete_message(self, *args, **kwargs):
        self.deleted += 1


class FakeContext:
    def __init__(self, urls=None):
        self.user_data = {"video_urls": urls or []}
        self.sent_messages = []

        context = self

        class Bot:
            async def send_message(self, chat_id, text, **kwargs):
                message = FakeMessage(text)
                context.sent_messages.append((chat_id, text, message))
                return message

        self.bot = Bot()


def make_update(text="", query=None):
    message = FakeMessage(text)
    return SimpleNamespace(
        message=message,
        callback_query=query or FakeQuery(),
        effective_user=SimpleNamespace(id=1, username="tester", first_name="Tester"),
        effective_chat=SimpleNamespace(id=99),
    )


def test_extract_urls_deduplicates_and_strips_punctuation():
    text = "https://example.com/a, https://example.com/b. https://example.com/a"
    assert extract_urls(text) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_extract_urls_preserves_more_than_five_for_handler_limit_check():
    urls = extract_urls(" ".join(f"https://example.com/{i}" for i in range(6)))
    assert len(urls) == 6


def test_single_url_delegates_unchanged():
    calls = []

    async def original_message(update, context):
        calls.append(update)

    bot = make_bot_module(message_impl=original_message)
    install(bot)
    context = FakeContext()
    update = make_update("https://example.com/one")

    run(bot.handle_message(update, context))

    assert calls == [update]
    assert context.user_data == {}


def test_more_than_five_urls_are_rejected_without_calling_original_handler():
    calls = []

    async def original_message(update, context):
        calls.append(update)

    bot = make_bot_module(message_impl=original_message)
    install(bot)
    context = FakeContext()
    update = make_update(" ".join(f"https://example.com/{i}" for i in range(6)))

    run(bot.handle_message(update, context))

    assert calls == []
    assert update.message.replies
    assert "Maximum 5" in update.message.replies[0][0]


def test_one_valid_url_from_mixed_input_uses_original_single_url_path():
    seen = []

    async def original_message(update, context):
        seen.append(update.message.text)

    bot = make_bot_module(message_impl=original_message)
    install(bot)
    context = FakeContext()
    update = make_update(
        "https://example.com/valid https://invalid.example/invalid"
    )

    run(bot.handle_message(update, context))

    assert seen == ["https://example.com/valid"]


def test_batch_download_runs_all_urls_in_order_and_routes_later_ui_to_status_messages():
    calls = []
    later_status_messages = []

    async def original_download(update, context):
        calls.append(context.user_data["video_url"])
        if len(calls) > 1:
            status = update.callback_query.message
            later_status_messages.append(status)
            await update.callback_query.edit_message_text("processing")
            await update.callback_query.delete_message()

    bot = make_bot_module(download_impl=original_download)
    install(bot)

    query = FakeQuery("video_720")
    update = make_update(query=query)
    context = FakeContext([
        "https://example.com/1",
        "https://example.com/2",
        "https://example.com/3",
    ])

    run(bot.download_media(update, context))

    assert calls == [
        "https://example.com/1",
        "https://example.com/2",
        "https://example.com/3",
    ]
    assert len(context.sent_messages) == 2
    assert len(later_status_messages) == 2
    assert later_status_messages[0].edits == [("processing", {})]
    assert later_status_messages[0].deleted is True
    assert query.answers == 0
    assert context.user_data == {}


def test_batch_cleanup_runs_when_original_download_raises():
    calls = []

    async def original_download(update, context):
        calls.append(context.user_data["video_url"])
        if len(calls) == 2:
            raise RuntimeError("expected test failure")

    bot = make_bot_module(download_impl=original_download)
    install(bot)

    update = make_update(query=FakeQuery("video_720"))
    context = FakeContext([
        "https://example.com/1",
        "https://example.com/2",
        "https://example.com/3",
    ])

    try:
        run(bot.download_media(update, context))
    except RuntimeError as exc:
        assert str(exc) == "expected test failure"
    else:
        raise AssertionError("expected RuntimeError")

    assert calls == [
        "https://example.com/1",
        "https://example.com/2",
    ]
    assert context.user_data == {}
