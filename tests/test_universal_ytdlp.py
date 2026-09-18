import json

import pytest

from downloader import universal_ytdlp


def test_extract_with_yt_dlp_rejects_non_object_metadata(monkeypatch):
    class Completed:
        returncode = 0
        stdout = json.dumps([{"url": "https://example.com/video.mp4"}])
        stderr = ""

    monkeypatch.setattr(universal_ytdlp.subprocess, "run", lambda *args, **kwargs: Completed())

    with pytest.raises(RuntimeError, match="unexpected metadata shape"):
        universal_ytdlp.extract_with_yt_dlp("https://example.com/post")
