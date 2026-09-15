from downloader.krx18_resolver import identity_score


def test_krx18_identity_accepts_movie_id_in_player_target_url():
    source = "https://krx18.com/movies/84170-example-movie/"
    target = "https://playkrx18.site/watch/84170"
    assert identity_score(source, "", target, "Example Movie") >= 100


def test_krx18_identity_rejects_unrelated_player_target():
    source = "https://krx18.com/movies/84170-example-movie/"
    target = "https://example-player.test/watch/99999"
    assert identity_score(source, "unrelated content", target, "Example Movie") < 40


def test_krx18_identity_accepts_matching_title_evidence_without_id():
    source = "https://krx18.com/movies/84170-example-movie/"
    assert identity_score(source, "Example Movie full movie", "https://playkrx18.site/watch", "Example Movie") >= 40
