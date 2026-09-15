from __future__ import annotations

from types import SimpleNamespace

from downloader.shahid4u_resolver import extract_quality
from downloader.smart_media_bridge import (
    _order_shahid_candidates,
    _preflight_candidate,
    _requested_quality,
)


def test_extract_quality_preserves_explicit_page_quality():
    assert extract_quality("تحميل مباشر 1080p") == 1080
    assert extract_quality("server-720") == 720
    assert extract_quality("no explicit quality") is None


def test_requested_quality_is_derived_from_existing_format_option():
    assert _requested_quality("bestvideo[height<=720]+bestaudio/best[height<=720]") == 720
    assert _requested_quality("bestvideo+bestaudio/best") is None


def test_shahid_candidate_order_prefers_requested_quality_then_lower_quality():
    candidates = [
        {"url": "https://cdn.example/1080p.mp4", "quality": 1080, "score": 150},
        {"url": "https://cdn.example/480p.mp4", "quality": 480, "score": 120},
        {"url": "https://cdn.example/720p.mp4", "quality": 720, "score": 130},
        {"url": "https://cdn.example/360p.mp4", "quality": 360, "score": 110},
    ]
    ordered = _order_shahid_candidates(candidates, 720)
    assert [item["quality"] for item in ordered] == [720, 480, 360, 1080]


def test_shahid_candidate_order_for_best_prefers_highest_quality():
    candidates = [
        {"url": "https://cdn.example/480p.mp4", "quality": 480, "score": 120},
        {"url": "https://cdn.example/1080p.mp4", "quality": 1080, "score": 110},
        {"url": "https://cdn.example/720p.mp4", "quality": 720, "score": 150},
    ]
    ordered = _order_shahid_candidates(candidates, None)
    assert [item["quality"] for item in ordered] == [1080, 720, 480]


def test_preflight_accepts_known_content_length_without_reading_body():
    calls = []

    class Response:
        headers = {"Content-Length": "104857600"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def request(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(url=url)

    def safe_open(_request, **kwargs):
        assert kwargs["max_bytes"] == 2 * 1024 * 1024 * 1024
        return Response()

    bot = SimpleNamespace(Request=request, safe_urlopen=safe_open)
    accepted, size, reason = _preflight_candidate(
        bot,
        "https://cdn.example/720p.mp4",
        max_bytes=2 * 1024 * 1024 * 1024,
    )

    assert accepted is True
    assert size == 104857600
    assert reason == "content_length"
    assert calls[0]["method"] == "HEAD"


def test_preflight_rejects_known_oversized_head_response_before_download():
    class Response:
        headers = {"Content-Length": str(3 * 1024 * 1024 * 1024)}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def request(url, **kwargs):
        return SimpleNamespace(url=url, method=kwargs.get("method"))

    def safe_open(_request, **kwargs):
        # The production safe_urlopen rejects an oversized declared response
        # before any body is consumed. This fake models that exact contract.
        if kwargs["max_bytes"] == 2 * 1024 * 1024 * 1024:
            raise ValueError("Response exceeds configured size limit")
        return Response()

    bot = SimpleNamespace(Request=request, safe_urlopen=safe_open)
    accepted, size, reason = _preflight_candidate(
        bot,
        "https://cdn.example/1080p.mp4",
        max_bytes=2 * 1024 * 1024 * 1024,
    )

    assert accepted is False
    assert size > 2 * 1024 * 1024 * 1024
    assert reason == "head_response_exceeds_limit"
