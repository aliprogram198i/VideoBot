from downloader import shhaiid4u_browser_provider_handoff as handoff


def test_provider_host_allowlist():
    assert handoff.is_supported_provider("https://megaup.net/a/video.mp4")
    assert handoff.is_supported_provider("https://streamtape.com/v/abc")
    assert handoff.is_supported_provider("https://cdn.megaup.net/a/video.mp4")
    assert not handoff.is_supported_provider("https://example.com/video.mp4")


def test_candidate_filter_is_bounded_and_provider_only():
    candidates = handoff._owned_candidates([
        "https://example.com/video.mp4",
        "https://megaup.net/a.mp4",
        "https://megaup.net/a.mp4",
        "https://streamtape.com/v/abc",
    ])
    assert candidates == [
        "https://megaup.net/a.mp4",
        "https://streamtape.com/v/abc",
    ]


def test_candidate_limit():
    values = [f"https://megaup.net/{index}.mp4" for index in range(20)]
    assert len(handoff._owned_candidates(values)) == handoff.MAX_CANDIDATES


def test_install_is_idempotent():
    class Bot:
        async def download_with_fallback(*args, **kwargs):
            return None

    bot = Bot()
    handoff.install(bot)
    wrapped = bot.download_with_fallback
    handoff.install(bot)
    assert bot.download_with_fallback is wrapped
