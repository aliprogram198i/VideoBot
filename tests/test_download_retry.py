from types import SimpleNamespace

import pytest

from plugins import download_retry


class _FakeQuery:
    def __init__(self, data="video_720"):
        self.data = data
        self.from_user = SimpleNamespace(id=123)
        self.message = SimpleNamespace()
        self.edits = []
        self.answered = 0
        self.deleted = 0

    async def answer(self, *args, **kwargs):
        self.answered += 1

    async def edit_message_text(self, *args, **kwargs):
        self.edits.append((args, kwargs))

    async def delete_message(self, *args, **kwargs):
        self.deleted += 1


class _FakeContext:
    def __init__(self):
        self.user_data = {}


class _FakeBotModule:
    TEXTS = {
        "ar": {
            "download_error": "download failed",
            "file_error": "file missing",
            "general_error": "general failure",
            "expired": "expired",
            "banned": "banned",
        }
    }

    @staticmethod
    def get_language(_user_id):
        return "ar"


def test_quality_callback_validation_is_bounded():
    assert download_retry._QUALITY_CALLBACK.fullmatch("video_720")
    assert download_retry._QUALITY_CALLBACK.fullmatch("audio_320")
    assert not download_retry._QUALITY_CALLBACK.fullmatch("video_menu")
    assert not download_retry._QUALITY_CALLBACK.fullmatch("retry_download_1")


def test_retry_markup_has_stable_callback():
    markup = download_retry._retry_markup("ar")
    button = markup.inline_keyboard[0][0]
    assert button.callback_data == "download_retry"
    assert button.text == "🔄 إعادة المحاولة"


@pytest.mark.asyncio
async def test_failure_proxy_adds_retry_button_without_changing_failure_text():
    query = _FakeQuery()
    context = _FakeContext()
    context.user_data[download_retry._STATE_KEY] = {
        "url": "https://example.com/video",
        "choice": "video_720",
        "retries": 0,
    }
    proxy = download_retry._RetryQueryProxy(query, context, _FakeBotModule())

    await proxy.edit_message_text("download failed")

    args, kwargs = query.edits[-1]
    assert args[0] == "download failed"
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "download_retry"
    assert context.user_data[download_retry._STATE_KEY]["retries"] == 0


@pytest.mark.asyncio
async def test_non_failure_edit_does_not_get_retry_button():
    query = _FakeQuery()
    context = _FakeContext()
    context.user_data[download_retry._STATE_KEY] = {
        "url": "https://example.com/video",
        "choice": "video_720",
        "retries": 0,
    }
    proxy = download_retry._RetryQueryProxy(query, context, _FakeBotModule())

    await proxy.edit_message_text("loading")

    _, kwargs = query.edits[-1]
    assert "reply_markup" not in kwargs


@pytest.mark.asyncio
async def test_successful_delete_clears_retry_state():
    query = _FakeQuery()
    context = _FakeContext()
    context.user_data[download_retry._STATE_KEY] = {"url": "https://example.com/video", "choice": "video_720", "retries": 0}
    proxy = download_retry._RetryQueryProxy(query, context, _FakeBotModule())

    await proxy.delete_message()

    assert download_retry._STATE_KEY not in context.user_data
    assert query.deleted == 1


@pytest.mark.asyncio
async def test_retry_callback_increments_attempt_and_reuses_exact_choice(monkeypatch):
    query = _FakeQuery()
    context = _FakeContext()
    context.user_data[download_retry._STATE_KEY] = {
        "url": "https://example.com/video",
        "choice": "video_720",
        "retries": 1,
    }
    update = SimpleNamespace(callback_query=query, effective_user=query.from_user)

    calls = []

    class BotModule(_FakeBotModule):
        @staticmethod
        def validate_public_http_url(url):
            assert url == "https://example.com/video"

        async def download_media(self, update, context):
            calls.append((update.callback_query.data, context.user_data["video_url"]))

    bot_module = BotModule()
    await download_retry._retry_callback(update, context, bot_module)

    assert calls == [("video_720", "https://example.com/video")]
    assert context.user_data[download_retry._STATE_KEY]["retries"] == 2
    assert query.answered == 0
