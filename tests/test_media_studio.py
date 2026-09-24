import asyncio
from pathlib import Path

import plugins.media_studio as studio


def test_studio_keyboard_callback_data_stays_within_telegram_limit():
    markup = studio.studio_keyboard("0123456789abcdef")
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]
    assert callbacks
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)


def test_cache_media_is_scoped_to_user(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "CACHE_DIR", tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")

    token = studio.cache_media_for_user(123, str(source))

    assert studio._cached_path(123, token) is not None
    assert studio._cached_path(456, token) is None


def test_create_mp3_uses_lame(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"input")
    captured = {}

    async def fake_run(*args):
        captured["args"] = args
        Path(args[-1]).write_bytes(b"output")

    monkeypatch.setattr(studio, "_run_ffmpeg", fake_run)

    output, media_type = asyncio.run(
        studio._create_result(source, "mp3", None)
    )

    assert media_type == "audio"
    assert output.suffix == ".mp3"
    assert "libmp3lame" in captured["args"]
    assert "320k" in captured["args"]


def test_create_trim_uses_accurate_reencode(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"input")
    captured = {}

    async def fake_run(*args):
        captured["args"] = args
        Path(args[-1]).write_bytes(b"output")

    monkeypatch.setattr(studio, "_run_ffmpeg", fake_run)

    output, media_type = asyncio.run(
        studio._create_result(source, "trim", "15")
    )

    assert media_type == "video"
    assert output.name.endswith("_15s.mp4")
    assert "-t" in captured["args"]
    assert "15" in captured["args"]
    assert "libx264" in captured["args"]
