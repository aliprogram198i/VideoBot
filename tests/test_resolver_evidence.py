import sqlite3

from downloader.resolver_evidence import ResolverEvidence, ResolverEvidenceStore


def test_paired_sample_records_once_and_aggregates(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    assert store.record_sample(
        "sample-1",
        platform="youtube",
        media_kind="progressive",
        outcomes=[
            ResolverEvidence("legacy_extractor", True, 100),
            ResolverEvidence("smart_media", False, 120),
            ResolverEvidence("browser_media", True, 300),
        ],
    )
    assert store.record_sample(
        "sample-2",
        platform="youtube",
        media_kind="progressive",
        outcomes=[
            ResolverEvidence("legacy_extractor", True, 110),
            ResolverEvidence("smart_media", True, 130),
            ResolverEvidence("browser_media", False, 310),
        ],
    )
    policy = store.paired_policy(platform="youtube", media_kind="progressive", min_paired_samples=2)
    assert {row["resolver"] for row in policy} == {"legacy_extractor", "smart_media", "browser_media"}
    legacy = next(row for row in policy if row["resolver"] == "legacy_extractor")
    assert legacy["paired_samples"] == 2
    assert legacy["success_rate"] == 1.0
    assert legacy["success_rate_lower_95"] < 1.0


def test_duplicate_resolver_or_sample_is_rejected(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    outcomes = [ResolverEvidence("legacy_extractor", True), ResolverEvidence("smart_media", False)]
    assert store.record_sample("sample-1", platform="other", media_kind="unknown", outcomes=outcomes)
    assert not store.record_sample("sample-1", platform="other", media_kind="unknown", outcomes=outcomes)
    assert not store.record_sample(
        "sample-2", platform="other", media_kind="unknown",
        outcomes=[ResolverEvidence("legacy_extractor", True), ResolverEvidence("legacy_extractor", False)],
    )


def test_invalid_values_fail_closed_without_partial_write(tmp_path):
    path = tmp_path / "evidence.db"
    store = ResolverEvidenceStore(path)
    assert not store.record_sample(
        "sample-1",
        platform="youtube",
        media_kind="progressive",
        outcomes=[ResolverEvidence("legacy_extractor", True, elapsed_ms=-1), ResolverEvidence("smart_media", True)],
    )
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM resolver_evidence").fetchone()[0] == 0


def test_policy_requires_paired_sample_threshold(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    assert store.record_sample(
        "sample-1", platform="facebook", media_kind="hls",
        outcomes=[ResolverEvidence("legacy_extractor", True), ResolverEvidence("smart_media", False)],
    )
    assert store.paired_policy(platform="facebook", media_kind="hls", min_paired_samples=2) == []


def test_context_is_bounded_to_allowlisted_classes(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    assert store.record_sample(
        "sample-1", platform="not-a-platform", media_kind="not-a-kind",
        outcomes=[ResolverEvidence("legacy_extractor", True), ResolverEvidence("smart_media", True)],
    )
    rows = store.paired_policy(platform="other", media_kind="other", min_paired_samples=1)
    assert len(rows) == 2
