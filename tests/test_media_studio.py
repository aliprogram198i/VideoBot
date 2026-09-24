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


def test_create_compress_escapes_scale_expression_comma(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"input")
    captured = {}

    async def fake_run(*args):
        captured["args"] = args
        Path(args[-1]).write_bytes(b"output")

    monkeypatch.setattr(studio, "_run_ffmpeg", fake_run)

    output, media_type = asyncio.run(
        studio._create_result(source, "compress", None)
    )

    assert media_type == "video"
    assert output.name.endswith("_compressed.mp4")
    assert "-vf" in captured["args"]
    filter_index = captured["args"].index("-vf")
    assert captured["args"][filter_index + 1] == r"scale=min(720\,iw):-2"
    assert "libx264" in captured["args"]


def _capture_ffmpeg(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(*args):
        captured["args"] = args
        Path(args[-1]).write_bytes(b"output")

    monkeypatch.setattr(studio, "_run_ffmpeg", fake_run)
    return captured


def test_audio_format_variants(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"input")

    for value, suffix, codec, bitrate in (
        ("m4a", ".m4a", "aac", "192k"),
        ("opus", ".opus", "libopus", "160k"),
    ):
        captured = _capture_ffmpeg(monkeypatch, tmp_path)
        output, media_type = asyncio.run(
            studio._create_result(source, "audio", value)
        )
        assert media_type == "audio"
        assert output.suffix == suffix
        assert codec in captured["args"]
        assert bitrate in captured["args"]


def test_custom_trim_parses_and_limits_duration():
    assert studio._parse_custom_trim("00:10 - 00:40") == (10.0, 30.0)
    assert studio._parse_custom_trim("1:02:03 - 1:02:33") == (3723.0, 30.0)

    try:
        studio._parse_custom_trim("00:00 - 05:01")
    except ValueError as exc:
        assert str(exc) == "media_studio_trim_limit"
    else:
        raise AssertionError("Expected custom trim duration limit")


def test_custom_trim_builds_ffmpeg_range(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"input")
    captured = _capture_ffmpeg(monkeypatch, tmp_path)

    output, media_type = asyncio.run(
        studio._create_result(source, "trimcustom", "00:10 - 00:40")
    )

    assert media_type == "video"
    assert output.name.endswith("_custom.mp4")
    assert captured["args"][captured["args"].index("-ss") + 1] == "10.000"
    assert captured["args"][captured["args"].index("-t") + 1] == "30.000"


def test_resize_uses_requested_height(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"input")
    captured = _capture_ffmpeg(monkeypatch, tmp_path)

    asyncio.run(studio._create_result(source, "resize", "720"))

    filter_index = captured["args"].index("-vf")
    assert captured["args"][filter_index + 1] == "scale=-2:720"


def test_preset_vertical_filter_is_valid(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"input")
    captured = _capture_ffmpeg(monkeypatch, tmp_path)

    asyncio.run(studio._create_result(source, "preset", "reels"))

    filter_index = captured["args"].index("-vf")
    filter_graph = captured["args"][filter_index + 1]
    assert "scale=720:1280:force_original_aspect_ratio=decrease" in filter_graph
    assert "pad=720:1280" in filter_graph


def test_volume_levels_and_mute(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"input")

    captured = _capture_ffmpeg(monkeypatch, tmp_path)
    asyncio.run(studio._create_result(source, "volume", "150"))
    assert "-af" in captured["args"]
    assert "volume=1.5" in captured["args"]

    captured = _capture_ffmpeg(monkeypatch, tmp_path)
    asyncio.run(studio._create_result(source, "volume", "0"))
    assert "-an" in captured["args"]
