from downloader import shhaiid4u_provider_router as router


def test_provider_identity_is_hostname_based():
    assert router.identify_provider("https://megaup.net/v/abc").provider_id == "megaup"
    assert router.identify_provider("https://cdn.megaup.net/v/abc").provider_id == "megaup"
    assert router.identify_provider("https://streamtape.com/v/abc").provider_id == "streamtape"
    assert router.identify_provider("https://example.com/video.mp4") is None
    assert router.identify_provider("https://shhaiid4u.net/watch/abc") is None


def test_provider_routing_preserves_provider_order_and_deduplicates():
    values = [
        "https://streamtape.com/v/1",
        "https://megaup.net/v/2",
        "https://streamtape.com/v/1",
        "https://example.com/v/3",
        "https://cdn.megaup.net/v/4",
    ]
    assert router.owned_candidates(values) == [
        "https://megaup.net/v/2",
        "https://cdn.megaup.net/v/4",
        "https://streamtape.com/v/1",
    ]


def test_unknown_providers_are_excluded():
    values = [
        "https://doodstream.com/e/abc",
        "https://vidaraa.com/v/abc",
        "https://voe.sx/e/abc",
    ]
    assert router.route_candidates(values) == {}
    assert router.owned_candidates(values) == []


def test_install_is_idempotent():
    class Bot:
        MAX_VIDEO_DOWNLOAD_BYTES = 500 * 1024 * 1024
        MAX_AUDIO_DOWNLOAD_BYTES = 500 * 1024 * 1024

        async def download_with_fallback(*args, **kwargs):
            return "original"

    router.install(Bot)
    first = Bot.download_with_fallback
    router.install(Bot)
    assert Bot.download_with_fallback is first
    assert getattr(Bot.download_with_fallback, "_shhaiid4u_provider_router", False)
