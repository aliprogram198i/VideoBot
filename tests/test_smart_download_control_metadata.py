from plugins.smart_download_control import _dimensions, _downloadable_quality, _views


def test_link_info_dimensions_are_not_mislabeled_as_progressive_quality():
    data = {"width": 1440, "height": 2560}
    assert _dimensions(data) == "1440×2560"


def test_downloadable_quality_uses_only_video_formats_with_urls():
    data = {
        "formats": [
            {"url": "https://example.test/video", "vcodec": "avc1", "width": 1280, "height": 720},
            {"url": "https://example.test/video2", "vcodec": "avc1", "width": 1440, "height": 2560},
            {"url": "https://example.test/audio", "vcodec": "none", "acodec": "mp4a", "width": 9999, "height": 9999},
            {"vcodec": "avc1", "width": 9999, "height": 9999},
        ]
    }
    assert _downloadable_quality(data) == "1440×2560"


def test_views_remain_a_single_canonical_value():
    assert _views(978000) == "978.0K"
    assert _views(560000) == "560.0K"
