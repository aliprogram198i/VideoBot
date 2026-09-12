from downloader.shhaiid4u_provider_adapters import adapter_for


def test_megaup_adapter_is_selected():
    adapter = adapter_for("https://megaup.net/example/video.mp4")
    assert adapter is not None
    assert adapter.hostname == "megaup.net"
    assert adapter.settle_ms >= 5000


def test_streamtape_adapter_normalizes_embed_path():
    adapter = adapter_for("https://streamtape.com/e/ABC123")
    assert adapter is not None
    assert adapter.normalize("https://streamtape.com/e/ABC123") == "https://streamtape.com/v/ABC123"


def test_unowned_provider_is_not_selected():
    assert adapter_for("https://example.com/video.mp4") is None
