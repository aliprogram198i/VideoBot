import asyncio

from downloader import browser_media_resolver


def test_resolve_awaits_async_worker_inside_existing_event_loop(monkeypatch):
    monkeypatch.setenv("ALIBOT_BROWSER_RESOLVER", "1")
    calls = []

    async def fake_resolve_async(url, **kwargs):
        calls.append((url, kwargs))
        return ["https://cdn.example/video.mp4"]

    monkeypatch.setattr(browser_media_resolver, "_resolve_async", fake_resolve_async)

    async def scenario():
        return await browser_media_resolver.resolve(
            "https://example.com/watch/1",
            validator=lambda value: None,
        )

    result = asyncio.run(scenario())

    assert result == ["https://cdn.example/video.mp4"]
    assert calls and calls[0][0] == "https://example.com/watch/1"
