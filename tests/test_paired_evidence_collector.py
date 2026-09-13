import asyncio
import random

from downloader import paired_evidence_collector as collector


class _Store:
    def __init__(self):
        self.calls = []

    def record_sample(self, sample_id, *, platform, media_kind, outcomes):
        self.calls.append((sample_id, platform, media_kind, outcomes))
        return True


def test_enabled_requires_staging_and_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ALIBOT_PAIRED_EVIDENCE_ENABLED", raising=False)
    monkeypatch.setenv("ALIBOT_RUNTIME_ENV", "staging")
    assert collector.enabled() is False

    monkeypatch.setenv("ALIBOT_PAIRED_EVIDENCE_ENABLED", "1")
    assert collector.enabled() is True

    monkeypatch.setenv("ALIBOT_RUNTIME_ENV", "production")
    assert collector.enabled() is False


def test_collect_records_paired_outcomes(monkeypatch):
    monkeypatch.setenv("ALIBOT_PAIRED_EVIDENCE_ENABLED", "1")
    monkeypatch.setenv("ALIBOT_RUNTIME_ENV", "staging")
    monkeypatch.setenv("ALIBOT_PAIRED_EVIDENCE_RATE", "1")

    async def first(url):
        return [url]

    async def second(url):
        return []

    store = _Store()
    result = asyncio.run(
        collector.collect(
            store,
            sample_id="sample-1",
            platform="youtube",
            media_kind="unknown",
            source_url="https://youtube.com/watch?v=x",
            resolvers={"smart_media": first, "browser_media": second},
            rng=random.Random(0),
        )
    )

    assert result is True
    assert len(store.calls) == 1
    sample_id, platform, media_kind, outcomes = store.calls[0]
    assert sample_id == "sample-1"
    assert platform == "youtube"
    assert media_kind == "unknown"
    assert [item.resolver for item in outcomes] == ["smart_media", "browser_media"]
    assert [item.success for item in outcomes] == [True, False]


def test_collect_is_fail_open_on_probe_errors(monkeypatch):
    monkeypatch.setenv("ALIBOT_PAIRED_EVIDENCE_ENABLED", "1")
    monkeypatch.setenv("ALIBOT_RUNTIME_ENV", "staging")
    monkeypatch.setenv("ALIBOT_PAIRED_EVIDENCE_RATE", "1")

    async def broken(url):
        raise RuntimeError("probe failed")

    async def healthy(url):
        return [url]

    store = _Store()
    result = asyncio.run(
        collector.collect(
            store,
            sample_id="sample-2",
            platform="instagram",
            media_kind="iframe",
            source_url="https://instagram.com/reel/x",
            resolvers={"smart_media": broken, "cobalt": healthy},
            rng=random.Random(0),
        )
    )

    assert result is True
    assert len(store.calls) == 1
    outcomes = store.calls[0][3]
    assert outcomes[0].success is False
    assert outcomes[0].failure_reason == "RuntimeError"
    assert outcomes[1].success is True
