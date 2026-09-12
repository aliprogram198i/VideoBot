from downloader.adaptive_download_orchestrator import order_candidates


def test_order_candidates_prefers_hls_without_changing_values():
    candidates = [
        "https://cdn.example/video.mp4",
        "https://cdn.example/master.m3u8",
        "https://cdn.example/player/123",
    ]
    weights = {"kind:hls": 30.0, "kind:progressive": 20.0, "kind:iframe": 0.0}

    ordered = order_candidates(candidates, weights)

    assert set(ordered) == set(candidates)
    assert ordered[0].endswith("master.m3u8")


def test_order_candidates_is_stable_for_equal_scores():
    candidates = ["https://a.example/x", "https://b.example/y"]
    assert order_candidates(candidates, {}) == candidates


def test_order_candidates_preserves_short_input_identity():
    candidates = ["https://cdn.example/video.mp4"]
    assert order_candidates(candidates, {"kind:progressive": 20.0}) == candidates


def test_order_candidates_supports_mapping_candidates():
    candidates = [
        {"url": "https://cdn.example/video.mp4", "label": "progressive"},
        {"url": "https://cdn.example/master.m3u8", "label": "hls"},
    ]
    ordered = order_candidates(candidates, {"kind:hls": 30.0, "kind:progressive": 20.0})
    assert ordered[0]["label"] == "hls"
    assert ordered[1]["label"] == "progressive"


def test_order_candidates_keeps_non_urls_safe():
    candidates = [None, "not-a-url", "https://cdn.example/master.m3u8"]
    ordered = order_candidates(candidates, {"kind:hls": 30.0})
    assert ordered[0] == "https://cdn.example/master.m3u8"
    assert set(ordered) == set(candidates)


def test_order_candidates_bounds_adaptive_prefix_without_loss():
    candidates = [f"https://cdn.example/{index}.mp4" for index in range(20)]
    weights = {"kind:progressive": 10.0, "kind:hls": 100.0}
    candidates[15] = "https://cdn.example/master.m3u8"

    ordered = order_candidates(candidates, weights)

    # Candidate 15 is outside the adaptive prefix and must not be promoted.
    assert ordered[15].endswith("master.m3u8")
    assert ordered == [*candidates[:15], candidates[15], *candidates[16:]]
    assert len(ordered) == len(candidates)
    assert set(ordered) == set(candidates)
