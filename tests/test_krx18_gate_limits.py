from downloader.movie_source_guard import _KRX18_MIN_DURATION


def test_krx18_gate_uses_conservative_short_media_floor():
    assert _KRX18_MIN_DURATION == 20.0
