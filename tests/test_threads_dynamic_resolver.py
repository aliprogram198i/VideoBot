import json

import downloader.threads_extractor as threads_extractor


def test_share_link_uses_dynamic_resolver_when_page_has_no_media(monkeypatch):
    payload = {
        "id": "post-id",
        "medias": [
            {
                "is_video": True,
                "url": "https://scontent.example.com/video/abc123",
            }
        ],
    }

    class FakeResponse:
        status = 200

        class Headers:
            @staticmethod
            def get_content_type():
                return "application/json"

        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return json.dumps(payload).encode()[:size]

    monkeypatch.setattr(threads_extractor, "urlopen", lambda *args, **kwargs: FakeResponse())

    candidates = threads_extractor.extract_threads_media(
        "<html><body>client rendered</body></html>",
        "https://www.threads.com/@user/post/ABC123",
        source_url="https://www.threads.com/share/_6pSF8qq6/",
    )

    assert len(candidates) == 1
    assert candidates[0].url == "https://scontent.example.com/video/abc123"
    assert candidates[0].kind == "progressive"
    assert candidates[0].discovered_by == "threads_dynamic_resolver"


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
