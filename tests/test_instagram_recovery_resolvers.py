from downloader.cobalt_instagram import _is_instagram_post_url
from downloader.instagram_relay_html import _extract_media_from_html


TARGET = "https://www.instagram.com/p/Dcs0funuV-t/?stkn=dGsxaTVmcjM1bzJp"


def test_instagram_target_is_canonical_public_post_url():
    assert _is_instagram_post_url(TARGET)
    assert not _is_instagram_post_url("https://www.instagram.com/p/Dcs0funuV-t/other")


def test_relay_html_requires_exact_shortcode_and_extracts_video():
    html = """
    <html><script>
    var payload = {"code":"Dcs0funuV-t","video_versions":[
      {"url":"https://cdn.example.test/exact.mp4"}
    ]};
    </script></html>
    """
    found = _extract_media_from_html(html, "Dcs0funuV-t")
    assert found is not None
    urls, selection = found
    assert urls == ["https://cdn.example.test/exact.mp4"]
    assert selection["selection"] == "video_versions"


def test_relay_html_does_not_accept_neighbor_post():
    html = """
    <html><script>
    var payload = {"code":"OTHER123","video_versions":[
      {"url":"https://cdn.example.test/wrong.mp4"}
    ]};
    </script></html>
    """
    assert _extract_media_from_html(html, "Dcs0funuV-t") is None
