from types import SimpleNamespace

from downloader.instagram_identity import candidate_matches_instagram_source, parse_instagram_post_url
from downloader.telegram_identity import candidate_matches_telegram_source, parse_telegram_post_url


def test_telegram_wrong_neighbor_post_is_rejected():
    requested = parse_telegram_post_url("https://t.me/examplechannel/123")
    candidate = SimpleNamespace(
        source_page="https://t.me/examplechannel/124",
        metadata={"telegram_data_post": "examplechannel/124"},
    )
    assert requested is not None
    assert not candidate_matches_telegram_source(candidate, requested)


def test_telegram_exact_post_with_provenance_is_accepted():
    requested = parse_telegram_post_url("https://t.me/examplechannel/123")
    candidate = SimpleNamespace(
        source_page="https://t.me/examplechannel/123",
        metadata={"telegram_data_post": "examplechannel/123"},
    )
    assert requested is not None
    assert candidate_matches_telegram_source(candidate, requested)


def test_instagram_wrong_neighbor_reel_is_rejected():
    requested = parse_instagram_post_url("https://www.instagram.com/reel/Dcqf3AXNgfL/")
    candidate = SimpleNamespace(source_page="https://www.instagram.com/reel/OTHER123/")
    assert requested is not None
    assert not candidate_matches_instagram_source(candidate, requested)


def test_instagram_exact_reel_identity_is_accepted():
    requested = parse_instagram_post_url("https://www.instagram.com/reel/Dcqf3AXNgfL/")
    candidate = SimpleNamespace(source_page="https://www.instagram.com/reel/Dcqf3AXNgfL/?stkn=example")
    assert requested is not None
    assert candidate_matches_instagram_source(candidate, requested)
