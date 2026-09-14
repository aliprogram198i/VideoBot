import asyncio

import downloader.paired_evidence_collector as collector
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


def test_build_paired_probes_uses_explicit_legacy_hook(monkeypatch):
    calls = []

    async def legacy(url):
        calls.append(url)
        return [url]

    class Bot:
        _alibot_legacy_extractor_probe = staticmethod(legacy)

        @staticmethod
        def validate_public_http_url(url):
            return url

        @staticmethod
        def Request(*args, **kwargs):
            return None

        @staticmethod
        def safe_urlopen(*args, **kwargs):
            return None

        @staticmethod
        def read_limited(*args, **kwargs):
            return b""

    probes = observer._build_paired_probes(Bot())
    assert set(probes) == {"legacy_extractor", "smart_media", "browser_media", "cobalt"}
    assert asyncio.run(probes["legacy_extractor"]("https://example.com/video")) == ["https://example.com/video"]
    assert calls == ["https://example.com/video"]


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
    monkeypatch.setattr(observer, "_build_paired_probes", lambda bot: {})

    class Bot:
        async def extract(self, url):
            return [url]

    bot = Bot()
    bot.extract_direct_media_urls = bot.extract
    observer.install(bot)

    result = asyncio.run(bot.extract_direct_media_urls("https://youtube.com/watch?v=x"))
    assert result == ["https://youtube.com/watch?v=x"]
    assert len(records) == 1


def test_install_schedules_paired_collection_at_extraction_boundary(monkeypatch):
    monkeypatch.setenv("ALIBOT_RUNTIME_ENV", "staging")
    monkeypatch.setenv("ALIBOT_PAIRED_EVIDENCE_ENABLED", "1")
    monkeypatch.setattr(observer, "ResolverEvidenceStore", lambda: _FakeEvidence())
    monkeypatch.setattr(observer, "ResolverShadowDecisionStore", _FakeDecisions)
    monkeypatch.setattr(observer, "validate_resolver_set", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        observer,
        "evaluate_shadow",
        lambda order, validations, **kwargs: type("Decision", (), {"original_order": tuple(order), "proposed_order": tuple(order)})(),
    )

    async def fake_resolver(url, **kwargs):
        return [url]

    monkeypatch.setattr(
        observer,
        "_build_paired_probes",
        lambda bot: {"legacy_extractor": fake_resolver, "smart_media": fake_resolver},
    )
    monkeypatch.setattr(collector, "enabled", lambda: True)
    monkeypatch.setattr(collector, "sample_rate", lambda: 0.05)

    scheduled = []

    async def fake_collect(*args, **kwargs):
        scheduled.append((kwargs["platform"], kwargs["media_kind"], kwargs["source_url"]))
        return True

    monkeypatch.setattr(collector, "collect", fake_collect)

    async def exercise():
        class Bot:
            async def extract(self, url):
                return [url]

        bot = Bot()
        bot.extract_direct_media_urls = bot.extract
        observer.install(bot)
        result = await bot.extract_direct_media_urls("https://youtube.com/watch?v=x")
        await asyncio.sleep(0)
        return result

    result = asyncio.run(exercise())
    assert result == ["https://youtube.com/watch?v=x"]
    assert scheduled == [("youtube", "unknown", "https://youtube.com/watch?v=x")]


def test_observer_never_reorders_original_result(monkeypatch):
    monkeypatch.setattr(observer, "ResolverEvidenceStore", lambda: _FakeEvidence())
    monkeypatch.setattr(observer, "ResolverShadowDecisionStore", _FakeDecisions)
    monkeypatch.setattr(observer, "validate_resolver_set", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        observer,
        "evaluate_shadow",
        lambda order, validations, **kwargs: type("Decision", (), {"original_order": tuple(order), "proposed_order": ("cobalt", "legacy_extractor", "smart_media", "browser_media")})(),
    )
    monkeypatch.setattr(observer, "_build_paired_probes", lambda bot: {})

    class Bot:
        async def extract(self, url):
            return ["established", "order"]

    bot = Bot()
    bot.extract_direct_media_urls = bot.extract
    observer.install(bot)
    result = asyncio.run(bot.extract_direct_media_urls("https://youtube.com/watch?v=x"))
    assert result == ["established", "order"]
