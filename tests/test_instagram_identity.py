from types import SimpleNamespace

from downloader.instagram_identity import (
    candidate_matches_instagram_source,
    parse_instagram_post_url,
)


def test_parse_reel_with_query():
    identity = parse_instagram_post_url(
        "https://www.instagram.com/reel/Dcqf3AXNgfL/?stkn=example"
    )
    assert identity is not None
    assert identity.key == "Dcqf3AXNgfL"


def test_parse_p_and_tv_forms():
    assert parse_instagram_post_url(
        "https://instagram.com/p/AbC_123/"
    ).key == "AbC_123"
    assert parse_instagram_post_url(
        "https://www.instagram.com/tv/AbC-123/"
    ).key == "AbC-123"


def test_reject_non_post_paths():
    assert parse_instagram_post_url("https://www.instagram.com/") is None
    assert parse_instagram_post_url(
        "https://www.instagram.com/explore/"
    ) is None
    assert parse_instagram_post_url(
        "https://www.instagram.com/reels/"
    ) is None


def test_matching_source_page_is_accepted():
    source = parse_instagram_post_url(
        "https://www.instagram.com/reel/DdYyj56qw54/"
    )
    candidate = SimpleNamespace(
        source_page="https://www.instagram.com/reel/DdYyj56qw54/?stkn=example"
    )
    assert candidate_matches_instagram_source(candidate, source)


def test_neighboring_or_foreign_source_is_rejected():
    source = parse_instagram_post_url(
        "https://www.instagram.com/reel/DdYyj56qw54/"
    )
    neighboring = SimpleNamespace(
        source_page="https://www.instagram.com/reel/Dcqf3AXNgfL/"
    )
    foreign = SimpleNamespace(
        source_page="https://example.com/video/"
    )
    assert not candidate_matches_instagram_source(neighboring, source)
    assert not candidate_matches_instagram_source(foreign, source)
