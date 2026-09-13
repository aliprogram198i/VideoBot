from downloader.resolver_shadow_decision import (
    ResolverShadowDecisionStore,
    evaluate_shadow,
)


ORDER = ["legacy_extractor", "smart_media", "browser_media", "cobalt"]


def _row(a, b, *, p=0.001, delta=0.20, lower=0.10, samples=40, a_only=12, b_only=4):
    return {
        "resolver_a": a,
        "resolver_b": b,
        "paired_samples": samples,
        "a_only": a_only,
        "b_only": b_only,
        "success_rate_delta": delta,
        "delta_lower_95": lower,
        "delta_upper_95": delta + 0.05,
        "mcnemar_p_value": p,
    }


def test_shadow_reports_hypothetical_reorder_without_mutating_input():
    validations = [
        _row("smart_media", "legacy_extractor"),
        _row("smart_media", "browser_media"),
        _row("smart_media", "cobalt"),
    ]
    original = list(ORDER)
    decision = evaluate_shadow(original, validations, platform="youtube", media_kind="progressive")
    assert original == ORDER
    assert decision.would_reorder is True
    assert decision.proposed_first == "smart_media"
    assert list(decision.original_order) == ORDER
    assert list(decision.proposed_order) == [
        "smart_media", "legacy_extractor", "browser_media", "cobalt"
    ]


def test_shadow_preserves_order_when_evidence_is_insufficient():
    validations = [
        _row("smart_media", "legacy_extractor", samples=29),
        _row("smart_media", "browser_media"),
        _row("smart_media", "cobalt"),
    ]
    decision = evaluate_shadow(ORDER, validations)
    assert decision.would_reorder is False
    assert list(decision.proposed_order) == ORDER


def test_shadow_preserves_order_on_ambiguous_or_missing_peer_evidence():
    validations = [
        _row("smart_media", "legacy_extractor"),
        _row("smart_media", "browser_media"),
    ]
    decision = evaluate_shadow(ORDER, validations)
    assert decision.would_reorder is False
    assert list(decision.proposed_order) == ORDER


def test_shadow_context_is_bounded_and_isolated():
    decision = evaluate_shadow(
        ORDER,
        [],
        platform="not-a-real-platform",
        media_kind="not-a-real-kind",
    )
    assert decision.platform == "other"
    assert decision.media_kind == "other"


def test_shadow_store_persists_only_bounded_decision(tmp_path):
    store = ResolverShadowDecisionStore(tmp_path / "shadow.db")
    decision = evaluate_shadow(ORDER, [], platform="youtube", media_kind="hls")
    assert store.record(decision) is True
    summary = store.summary(platform="youtube", media_kind="hls")
    assert summary["decisions"] == 1
    assert summary["would_reorder"] == 0
    assert summary["reorder_rate"] == 0.0
    assert summary["proposed_first"] == [
        {"resolver": "legacy_extractor", "count": 1}
    ]


def test_shadow_store_rejects_order_set_mismatch(tmp_path):
    store = ResolverShadowDecisionStore(tmp_path / "shadow.db")
    decision = evaluate_shadow(ORDER, [])
    bad = decision.__class__(
        platform=decision.platform,
        media_kind=decision.media_kind,
        original_order=decision.original_order,
        proposed_order=("smart_media",),
        would_reorder=True,
        proposed_first="smart_media",
    )
    assert store.record(bad) is False
