import asyncio
import importlib
import sys
import types

import pytest


@pytest.fixture
def module(monkeypatch):
    telegram = types.ModuleType("telegram")
    telegram.InlineKeyboardButton = lambda *args, **kwargs: (args, kwargs)
    telegram.InlineKeyboardMarkup = lambda *args, **kwargs: (args, kwargs)
    telegram.Update = object
    monkeypatch.setitem(sys.modules, "telegram", telegram)

    telegram_ext = types.ModuleType("telegram.ext")
    telegram_ext.ApplicationHandlerStop = type("ApplicationHandlerStop", (Exception,), {})
    telegram_ext.CallbackQueryHandler = lambda *args, **kwargs: (args, kwargs)
    telegram_ext.CommandHandler = lambda *args, **kwargs: (args, kwargs)
    telegram_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    telegram_ext.MessageHandler = lambda *args, **kwargs: (args, kwargs)
    telegram_ext.filters = types.SimpleNamespace(TEXT=object(), COMMAND=object())
    monkeypatch.setitem(sys.modules, "telegram.ext", telegram_ext)

    return importlib.import_module("plugins.user_features")


def test_extract_urls_is_bounded_and_deduplicated(module):
    text = "https://example.com/a https://example.com/a https://example.org/b"
    assert module._extract_urls(text) == [
        "https://example.com/a",
        "https://example.org/b",
    ]


def test_batch_quality_pattern_accepts_only_existing_quality_routes(module):
    accepted = [
        "video_best", "video_1080", "video_720", "video_480", "video_360",
        "audio_best", "audio_320", "audio_256", "audio_192", "audio_128",
    ]
    rejected = ["video_2160", "audio_64", "main_menu", "video_menu"]
    for value in accepted:
        assert module.BATCH_QUALITY_RE.match(value)
    for value in rejected:
        assert not module.BATCH_QUALITY_RE.match(value)


def test_batch_limit_is_small_and_explicit(module):
    assert module.MAX_BATCH_URLS == 5
    assert module.MAX_URL_LENGTH == 2048


def test_per_user_lock_is_stable(module):
    first = module._lock_for(123)
    second = module._lock_for(123)
    assert first is second
    assert isinstance(first, asyncio.Lock)


def test_link_info_extracts_only_safe_display_metadata(module):
    payload = {
        "title": "  Example <Video>  ",
        "uploader": "Channel",
        "duration": 125,
        "view_count": 1234567,
        "upload_date": "20260915",
        "webpage_url": "https://example.com/video?token=secret",
        "url": "https://cdn.example.com/file.mp4?token=secret",
    }
    info = module._extract_info_payload(payload)
    assert info["title"] == "Example <Video>"
    assert info["uploader"] == "Channel"
    assert info["duration"] == "2:05"
    assert info["views"] == "1.2M"
    assert info["upload_date"] == "20260915"
    assert info["webpage_url"] == "https://example.com/video?token=secret"
    assert "url" not in info


def test_link_info_command_disables_playlist_and_download(module):
    command = module._info_command("https://example.com/video")
    assert command[:3] == ["python", "-m", "yt_dlp"]
    assert "--no-playlist" in command
    assert "--skip-download" in command
    assert "--dump-single-json" in command
    assert command[-1] == "https://example.com/video"


def test_link_info_limits_are_explicit(module):
    assert module.INFO_TIMEOUT_SECONDS == 25
    assert module.INFO_MAX_OUTPUT_BYTES == 2 * 1024 * 1024
