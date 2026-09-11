from downloader import shhaiid4u_provider_browser_handoff as bridge


def test_provider_ownership_isolated():
    assert bridge.is_supported_candidate("https://megaup.net/abc/video.mp4")
    assert bridge.is_supported_candidate("https://streamtape.com/v/abc")
    assert not bridge.is_supported_candidate("https://youtube.com/watch?v=abc")
    assert not bridge.is_supported_candidate("https://shhaiid4u.net/episode/abc")


def test_candidates_are_bounded_and_deduplicated():
    values = ["https://megaup.net/a.mp4", "https://megaup.net/a.mp4", "https://streamtape.com/v/x"] * 10
    result = bridge._owned_candidates(values)
    assert result == ["https://megaup.net/a.mp4", "https://streamtape.com/v/x"]
    assert len(result) <= bridge.MAX_CANDIDATES


def test_non_provider_candidates_are_rejected():
    assert bridge._owned_candidates(["https://example.com/video.mp4"]) == []


def test_local_file_must_stay_inside_temp_dir(tmp_path):
    inside = tmp_path / "video.mp4"
    inside.write_bytes(b"x")
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"x")
    try:
        assert bridge._local_file(str(inside), str(tmp_path))
        assert not bridge._local_file(str(outside), str(tmp_path))
    finally:
        outside.unlink(missing_ok=True)
