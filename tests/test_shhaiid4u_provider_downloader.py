from downloader import shhaiid4u_provider_downloader as provider


def test_provider_host_isolation():
    assert provider.is_supported_candidate("https://megaup.net/a/video.mp4")
    assert provider.is_supported_candidate("https://cdn.streamtape.com/v/abc")
    assert not provider.is_supported_candidate("https://example.com/video.mp4")
    assert not provider.is_supported_candidate("https://shhaiid4u.net/download/episode")


def test_provider_candidates_are_bounded_and_deduplicated():
    values = [
        "https://megaup.net/a.mp4",
        "https://megaup.net/a.mp4",
        "https://streamtape.com/v/one",
        "https://streamtape.com/v/two",
        "https://streamtape.com/v/three",
        "https://streamtape.com/v/four",
        "https://streamtape.com/v/five",
    ]
    result = provider._provider_candidates(values)
    assert len(result) == provider.MAX_PROVIDER_CANDIDATES
    assert len(result) == len(set(result))


def test_media_detection_accepts_extension_and_content_type():
    assert provider._looks_like_media("https://megaup.net/file.mp4")
    assert provider._looks_like_media(
        "https://streamtape.com/get_video?id=abc",
        "video/mp4; charset=binary",
    )
    assert not provider._looks_like_media("https://streamtape.com/v/abc", "text/html")


def test_streamtape_public_link_reconstruction():
    html = '''
    <script>document.getElementById('norobotlink').innerHTML = "?token=ABC123&expires=999";</script>
    <div id="ideoooolink" style="display:none;">//streamtape.com/get_video?id=XYZ</div>
    '''
    result = provider._extract_streamtape_direct_urls(
        html,
        "https://streamtape.com/v/XYZ",
    )
    assert result == [
        "https://streamtape.com/get_video?id=XYZ&token=ABC123&dl=1"
    ]


def test_streamtape_reconstruction_is_host_isolated():
    html = '<div id="ideoooolink">//example.com/video.mp4</div><script>token=ABC</script>'
    assert provider._extract_streamtape_direct_urls(html, "https://example.com/v/1") == []


def test_non_provider_urls_are_never_owned():
    values = [
        "https://shhaiid4u.net/episode/test",
        "https://youtube.com/watch?v=abc",
        "https://doubleclick.net/ad.mp4",
    ]
    assert provider._provider_candidates(values) == []


def test_install_is_idempotent():
    class Bot:
        MAX_VIDEO_DOWNLOAD_BYTES = 500 * 1024 * 1024
        MAX_AUDIO_DOWNLOAD_BYTES = 500 * 1024 * 1024

        async def download_with_fallback(*args, **kwargs):
            return "original"

    provider.install(Bot)
    first = Bot.download_with_fallback
    provider.install(Bot)
    assert Bot.download_with_fallback is first
    assert getattr(Bot.download_with_fallback, "_shhaiid4u_provider_downloader", False)
