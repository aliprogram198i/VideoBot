"""Regression tests for synchronous browser resolver calls from async contexts."""

import asyncio
import warnings
from unittest.mock import patch


def test_resolve_from_running_loop_uses_worker_and_awaits_once():
    from downloader.browser_media_resolver import resolve

    async def scenario():
        calls = 0

        async def fake_resolve_async(*args, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return ["https://example.com/video.mp4"]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with patch("downloader.browser_media_resolver._resolve_async", fake_resolve_async):
                result = resolve("https://example.com", validator=lambda _: None)
        return result, calls, caught

    result, calls, caught = asyncio.run(scenario())
    assert result == ["https://example.com/video.mp4"]
    assert calls == 1
    assert not [w for w in caught if "never awaited" in str(w.message)]


def test_worker_propagates_async_exception_without_unawaited_coroutine():
    from downloader.browser_media_resolver import _run_async_in_worker

    async def failing():
        await asyncio.sleep(0)
        raise ValueError("expected failure")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            _run_async_in_worker(lambda: failing())
        except ValueError as exc:
            assert str(exc) == "expected failure"
        else:
            raise AssertionError("ValueError was not raised")
    assert not [w for w in caught if "never awaited" in str(w.message)]


def test_worker_runs_and_returns_result():
    from downloader.browser_media_resolver import _run_async_in_worker

    async def successful():
        await asyncio.sleep(0)
        return ["ok"]

    assert _run_async_in_worker(lambda: successful()) == ["ok"]
