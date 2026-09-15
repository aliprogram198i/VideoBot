from downloader.krx18_resolver import identity_score, is_krx18_url


def test_krx18_host_scope():
    assert is_krx18_url("https://krx18.com/movies/84170-example/")
    assert is_krx18_url("https://www.krx18.com/movies/84170-example/")
    assert not is_krx18_url("https://example.com/movies/84170-example/")


def test_explicit_server_provenance_is_positive_identity_evidence():
    score = identity_score(
        "https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/",
        "generic player page",
        "https://playkrx18.site/watch/84170",
        "Femdom Deadly Thigh Squeeze Her Absolute Leg Scissors",
        explicit_server_provenance=True,
    )
    assert score >= 70


def test_unrelated_media_without_provenance_is_rejected_by_identity_score():
    score = identity_score(
        "https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/",
        "unrelated video advertisement",
        "https://cdn.example.invalid/video.mp4",
        "Femdom Deadly Thigh Squeeze Her Absolute Leg Scissors",
        explicit_server_provenance=False,
    )
    assert score < 70
