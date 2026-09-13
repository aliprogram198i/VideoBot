import asyncio

import downloader.shadow_runtime_observer as observer


class _FakeEvidence:
    db_path = "unused.db"


class _FakeDecisions:
    def __init__(self, path):
        self.records = []

    def record(self, decision):
        self.records.append(decision)
        return True


def test_source_url_resolution_is_bounded_to_explicit_url():
    assert observer._source_url((), {"url": "https://example.com/video"}) == "https://example.com/video"
    assert observer._source_url(("https://example.com/video",), {}) == "https://example.com/video"
    assert observer._source_url((123,), {}) is None


def test_install_preserves_result_and_observes_fail_open(monkeypatch):
    records = []

    class FakeDecisions:
        def __init__(self, path):
            self.path = path

        def record(self, decision):
            records.append(decision)
            return True

    monkeypatch.setattr(observer, "ResolverEvidenceStore", lambda: _FakeEvidence())
    monkeypatch.setattr(observer, "ResolverShadowDecisionStore", FakeDecisions)
    monkeypatch.setattr(observer, "validate_resolver_set", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        observer,
        "evaluate_shadow",
        lambda order, validations, **kwargs: type("Decision", (), {"original_order": tuple(order), "proposed_order": tuple(order)})(),
    )

    class Bot:
        async def extract(self, url):
            return [url]

    bot = Bot()
    bot.extract_direct_media_urls = bot.extract
    observer.install(bot)

    result = asyncio.run(bot.extract_direct_media_urls("https://youtube.com/watch?v=x"))
    assert result == ["https://youtube.com/watch?v=x"]
    assert len(records) == 1


def test_observer_never_reorders_original_result(monkeypatch):
    monkeypatch.setattr(observer, "ResolverEvidenceStore", lambda: _FakeEvidence())
    monkeypatch.setattr(observer, "ResolverShadowDecisionStore", _FakeDecisions)
    monkeypatch.setattr(observer, "validate_resolver_set", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        observer,
        "evaluate_shadow",
        lambda order, validations, **kwargs: type("Decision", (), {"original_order": tuple(order), "proposed_order": ("cobalt", "legacy_extractor", "smart_media", "browser_media")})(),
    )

    class Bot:
        async def extract(self, url):
            return ["established", "order"]

    bot = Bot()
    bot.extract_direct_media_urls = bot.extract
    observer.install(bot)
    result = asyncio.run(bot.extract_direct_media_urls("https://youtube.com/watch?v=x"))
    assert result == ["established", "order"]
