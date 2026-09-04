import json

import downloader.threads_extractor as threads_extractor


class FakeHeaders:
    def __init__(self, content_type):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class FakeResponse:
    def __init__(self, body, content_type="application/json", status=200):
        self.body = body
        self.status = status
        self.headers = FakeHeaders(content_type)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size):
        return self.body[:size]


def test_share_link_uses_dynamic_json_resolver(monkeypatch):
    payload = {
        "id": "post-id",
        "medias": [
            {
                "is_video": True,
                "url": "https://scontent.example.com/video/abc123",
            }
        ],
    }

    monkeypatch.setattr(
        threads_extractor,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(json.dumps(payload).encode()),
    )

    candidates = threads_extractor.extract_threads_media(
        "<html><body>client rendered</body></html>",
        "https://www.threads.com/@user/post/ABC123",
        source_url="https://www.threads.com/share/_6pSF8qq6/",
    )

    assert len(candidates) == 1
    assert candidates[0].url == "https://scontent.example.com/video/abc123"
    assert candidates[0].kind == "progressive"
    assert candidates[0].discovered_by == "threads_dynamic_resolver"


def test_share_link_falls_back_to_rendered_resolver_html(monkeypatch):
    html = """
    <html><head>
      <meta property="og:video" content="https://scontent.example.com/video/rendered123">
      <meta name="twitter:player:stream" content="https://scontent.example.com/video/rendered123">
    </head></html>
    """
    calls = []

    def fake_urlopen(request, **kwargs):
        calls.append(request.full_url)
        if "/api/share/" in request.full_url:
            return FakeResponse(b"not-json", content_type="text/html", status=200)
        return FakeResponse(html.encode(), content_type="text/html", status=200)

    monkeypatch.setattr(threads_extractor, "urlopen", fake_urlopen)

    candidates = threads_extractor.extract_threads_media(
        "<html><body>client rendered</body></html>",
        "https://www.threads.com/@user/post/ABC123",
        source_url="https://www.threads.com/share/BAYJmaXLha/",
    )

    assert candidates
    assert candidates[0].url == "https://scontent.example.com/video/rendered123"
    assert candidates[0].discovered_by == "threads_dynamic_resolver_html"
    assert any("/api/share/BAYJmaXLha" in url for url in calls)
    assert any("/share/BAYJmaXLha" in url for url in calls)


def test_non_share_page_does_not_call_dynamic_resolver(monkeypatch):
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("dynamic resolver should not be used")

    monkeypatch.setattr(threads_extractor, "urlopen", fail_if_called)

    candidates = threads_extractor.extract_threads_media(
        "<meta property='og:video' content='https://cdn.example.com/a.mp4'>",
        "https://www.threads.com/@user/post/ABC123",
    )

    assert candidates
    assert not called
