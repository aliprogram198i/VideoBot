from downloader.embed_resolver import EmbedResolver
from downloader.page_fetcher import FetchedPage, PageFetcher
from downloader.smart_extractor import MediaCandidate


def test_resolver_prefers_canonical_redirect_for_ytdlp(monkeypatch):
    calls = []

    def fake_fetch(url, *, timeout, max_bytes):
        return FetchedPage(
            url="https://www.facebook.com/reel/123456789/",
            html="<html><body>No direct media here</body></html>",
            content_type="text/html",
            status=200,
        )

    def fake_ytdlp(url):
        calls.append(url)
        return [
            MediaCandidate(
                url="https://video.example.test/media.mp4",
                kind="progressive",
                source_page=url,
                discovered_by="yt-dlp",
                score=0.9,
                metadata={},
            )
        ]

    monkeypatch.setattr(
        "downloader.embed_resolver.extract_with_yt_dlp",
        fake_ytdlp,
    )

    resolver = EmbedResolver(
        PageFetcher(fake_fetch),
        max_depth=1,
        max_pages=2,
        max_candidates=10,
    )
    result = resolver.resolve(
        "https://www.facebook.com/share/v/1Fu6HtxcPk/",
        timeout=5,
        max_html_bytes=1024 * 1024,
    )

    assert calls == ["https://www.facebook.com/reel/123456789/"]
    assert result.candidates
    assert result.candidates[0].url == "https://video.example.test/media.mp4"
