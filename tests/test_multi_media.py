from downloader.multi_media import (
    is_collection_candidate,
    normalize_entries,
)


def test_collection_candidate_known_post_shapes():
    assert is_collection_candidate("https://www.instagram.com/p/ABC123/")
    assert is_collection_candidate("https://www.facebook.com/reel/123/")
    assert is_collection_candidate("https://www.facebook.com/share/r/ABC/")
    assert not is_collection_candidate("https://youtu.be/GvjolgCoT5w")


def test_normalize_entries_deduplicates_and_rejects_untrusted_urls():
    entries = [
        {"webpage_url": "https://example.com/1", "title": "One", "ext": "mp4"},
        {"webpage_url": "https://example.com/1", "title": "Duplicate", "ext": "mp4"},
        {"webpage_url": "http://bad.local/2", "title": "Blocked", "ext": "jpg"},
        {"webpage_url": "https://example.com/3", "title": "Three", "ext": "jpg"},
    ]
    result = normalize_entries(
        entries,
        url_validator=lambda value: value.startswith("https://"),
    )
    assert [item.url for item in result] == [
        "https://example.com/1",
        "https://example.com/3",
    ]
    assert result[1].media_type == "image"


def test_normalize_entries_caps_items():
    entries = [
        {"webpage_url": f"https://example.com/{index}", "title": str(index)}
        for index in range(30)
    ]
    result = normalize_entries(
        entries,
        url_validator=lambda value: True,
        max_items=5,
    )
    assert len(result) == 5
    assert [item.index for item in result] == list(range(5))


def test_normalize_instagram_flat_playlist_entries_builds_child_urls_and_detects_images():
    entries = [
        {"id": "CHILD_IMAGE", "title": "Photo"},
        {"id": "CHILD_VIDEO", "title": "Video", "duration": 8, "vcodec": "h264"},
    ]
    result = normalize_entries(
        entries,
        url_validator=lambda value: value.startswith("https://www.instagram.com/p/"),
        parent_url="https://www.instagram.com/p/PARENT/",
    )
    assert [item.url for item in result] == [
        "https://www.instagram.com/p/CHILD_IMAGE/",
        "https://www.instagram.com/p/CHILD_VIDEO/",
    ]
    assert [item.media_type for item in result] == ["image", "video"]
