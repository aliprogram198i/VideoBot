from downloader.krx18_wp_public_sources import extract_rest_search_candidates, post_id_from_url


def test_post_id_and_slug_scope():
    url = "https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"
    assert post_id_from_url(url) == "84170"


def test_public_wp_search_candidates_are_normalized():
    data = [
        {
            "id": 84170,
            "type": "post",
            "subtype": "movie",
            "url": "https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/",
            "title": "Femdom Deadly Thigh Squeeze Her Absolute Leg Scissors",
        },
        {"id": "bad", "type": "post"},
    ]
    assert extract_rest_search_candidates(data) == [
        {
            "id": 84170,
            "type": "post",
            "subtype": "movie",
            "url": "https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/",
            "title": "Femdom Deadly Thigh Squeeze Her Absolute Leg Scissors",
        }
    ]
