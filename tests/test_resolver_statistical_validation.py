import pytest

from downloader.resolver_evidence import ResolverEvidence, ResolverEvidenceStore
from downloader.resolver_statistical_validation import validate_pair, validate_resolver_set


def _record_pair(store, sample_id, a_success, b_success):
    return store.record_sample(
        sample_id,
        platform="youtube",
        media_kind="progressive",
        outcomes=[
            ResolverEvidence("legacy_extractor", a_success),
            ResolverEvidence("smart_media", b_success),
        ],
    )


def test_validation_requires_minimum_paired_samples(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    for index in range(10):
        assert _record_pair(store, f"sample-{index}", True, False)
    result = validate_pair(
        store, "legacy_extractor", "smart_media",
        platform="youtube", media_kind="progressive",
        min_samples=20, min_discordant=5,
    )
    assert result.paired_samples == 10
    assert not result.validated_advantage


def test_validation_rejects_insufficient_discordant_pairs(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    for index in range(30):
        assert _record_pair(store, f"sample-{index}", True, True)
    result = validate_pair(
        store, "legacy_extractor", "smart_media",
        platform="youtube", media_kind="progressive",
        min_samples=20, min_discordant=5,
    )
    assert result.paired_samples == 30
    assert result.a_successes == result.b_successes == 30
    assert not result.validated_advantage
    assert result.mcnemar_p_value == 1.0


def test_validation_detects_strong_paired_advantage(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    for index in range(40):
        a_success = index < 32
        b_success = index < 12
        assert _record_pair(store, f"sample-{index}", a_success, b_success)
    result = validate_pair(
        store, "legacy_extractor", "smart_media",
        platform="youtube", media_kind="progressive",
        min_samples=30, min_discordant=10, min_effect=0.10,
    )
    assert result.a_successes == 32
    assert result.b_successes == 12
    assert result.success_rate_delta == 0.5
    assert result.statistically_significant
    assert result.practically_meaningful
    assert result.validated_advantage


def test_validation_is_directional(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    for index in range(40):
        assert _record_pair(store, f"sample-{index}", index < 12, index < 32)
    result = validate_pair(
        store, "legacy_extractor", "smart_media",
        platform="youtube", media_kind="progressive",
        min_samples=30, min_discordant=10, min_effect=0.10,
    )
    assert result.success_rate_delta == -0.5
    assert not result.validated_advantage


def test_validation_uses_identical_context_only(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    for index in range(40):
        assert _record_pair(store, f"youtube-{index}", True, False)
    assert store.record_sample(
        "facebook-1", platform="facebook", media_kind="progressive",
        outcomes=[ResolverEvidence("legacy_extractor", True), ResolverEvidence("smart_media", False)],
    )
    result = validate_pair(
        store, "legacy_extractor", "smart_media",
        platform="facebook", media_kind="progressive",
        min_samples=1, min_discordant=1,
    )
    assert result.paired_samples == 1


def test_validation_set_is_bounded_and_does_not_select(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    for index in range(2):
        assert store.record_sample(
            f"sample-{index}", platform="other", media_kind="unknown",
            outcomes=[
                ResolverEvidence("legacy_extractor", True),
                ResolverEvidence("smart_media", False),
                ResolverEvidence("browser_media", True),
            ],
        )
    results = validate_resolver_set(
        store,
        ["legacy_extractor", "smart_media", "browser_media", "legacy_extractor"],
        platform="other", media_kind="unknown",
        min_samples=1, min_discordant=1,
    )
    assert len(results) == 3
    assert all("validated_advantage" in item for item in results)
    assert not any("winner" in item for item in results)


def test_invalid_configuration_fails_closed(tmp_path):
    store = ResolverEvidenceStore(tmp_path / "evidence.db")
    with pytest.raises(ValueError):
        validate_pair(store, "legacy_extractor", "smart_media", alpha=0)
    with pytest.raises(ValueError):
        validate_pair(store, "legacy_extractor", "smart_media", min_effect=1.1)
    with pytest.raises(ValueError):
        validate_pair(store, "legacy_extractor", "legacy_extractor")
