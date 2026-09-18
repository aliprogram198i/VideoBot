from downloader.cobalt_instagram import _canonicalize_instagram_url


def test_instagram_query_padding_is_canonicalized():
    source = "https://www.instagram.com/reel/Dcqf3AXNgfL/?stkn=MXR6c2NkZGthN3N1OQ=="
    canonical = _canonicalize_instagram_url(source)

    assert canonical == (
        "https://www.instagram.com/reel/Dcqf3AXNgfL/"
        "?stkn=MXR6c2NkZGthN3N1OQ%3D%3D"
    )


def test_instagram_queryless_url_is_unchanged():
    source = "https://www.instagram.com/reel/Dcqf3AXNgfL/"
    assert _canonicalize_instagram_url(source) == source
