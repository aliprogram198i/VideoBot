from pathlib import Path

from downloader import movie_source_guard as guard


def test_social_sources_are_not_guarded():
    assert not guard.should_guard("https://www.youtube.com/watch?v=x")
    assert not guard.should_guard("https://www.tiktok.com/@x/video/1")


def test_movie_like_pages_are_guarded():
    assert guard.should_guard("https://example.test/watch/movie-name")
    assert guard.should_guard("https://shahid4u.run/episode/123")


def test_audio_never_guarded():
    assert not guard.should_guard(
        "https://example.test/watch/movie-name",
        is_audio=True,
    )


def test_ad_hosts_rejected():
    assert guard.is_ad_host("https://doubleclick.net/video.mp4")
    assert not guard.is_ad_host("https://cdn.example.test/video.mp4")


def test_missing_file_is_rejected(tmp_path: Path):
    result = guard.assess_local_media(
        str(tmp_path / "missing.mp4"),
        "https://example.test/watch/movie-name",
    )
    assert not result.accepted
    assert result.reason == "missing_file"
