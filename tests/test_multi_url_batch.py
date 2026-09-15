import asyncio

from plugins.multi_url_batch import extract_urls, install


def test_extract_urls_deduplicates_and_strips_punctuation():
    text = "https://example.com/a, https://example.com/b. https://example.com/a"
    assert extract_urls(text) == ["https://example.com/a", "https://example.com/b"]


def test_extract_urls_keeps_at_most_five_candidates_at_handler_boundary():
    urls = extract_urls(" ".join(f"https://example.com/{i}" for i in range(5)))
    assert len(urls) == 5


def test_batch_download_runs_all_urls_in_order():
    calls = []

    class Query:
        data = "video_720"
        from_user = object()
        message = object()

        async def answer(self, *args, **kwargs):
            pass

        async def edit_message_text(self, *args, **kwargs):
            pass

        async def delete_message(self, *args, **kwargs):
            pass

    class Update:
        callback_query = Query()

        @property
        def effective_chat(self):
            return type("Chat", (), {"id": 1})()

    class Context:
        user_data = {"video_urls": [
            "https://example.com/1",
            "https://example.com/2",
            "https://example.com/3",
        ]}

        class Bot:
            async def send_message(self, *args, **kwargs):
                pass

        bot = Bot()

    class BotModule:
        async def handle_message(self, update, context):
            pass

        async def download_media(self, update, context):
            calls.append(context.user_data["video_url"])

    bot = BotModule()
    install(bot)
    asyncio.run(bot.download_media(Update(), Context()))
    assert calls == [
        "https://example.com/1",
        "https://example.com/2",
        "https://example.com/3",
    ]
