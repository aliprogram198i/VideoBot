from downloader.resolver_selection_policy import choose_validated_first


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
        "validated_advantage": True,
    }


def test_selects_only_candidate_with_validated_advantage_over_all_peers():
    validations = [
        _row("smart_media", "legacy_extractor"),
        _row("smart_media", "browser_media"),
        _row("smart_media", "cobalt"),
    ]
    assert choose_validated_first(ORDER, validations) == [
        "smart_media", "legacy_extractor", "browser_media", "cobalt"
    ]


def test_reverse_direction_is_handled_without_requiring_rewritten_rows():
    validations = [
        _row("legacy_extractor", "smart_media", delta=-0.20, lower=-0.10, a_only=4, b_only=12),
        _row("browser_media", "smart_media", delta=-0.20, lower=-0.10, a_only=4, b_only=12),
        _row("cobalt", "smart_media", delta=-0.20, lower=-0.10, a_only=4, b_only=12),
    ]
    assert choose_validated_first(ORDER, validations) == [
        "smart_media", "legacy_extractor", "browser_media", "cobalt"
    ]


def test_insufficient_evidence_fails_closed():
    validations = [
        _row("smart_media", "legacy_extractor", samples=29),
        _row("smart_media", "browser_media"),
        _row("smart_media", "cobalt"),
    ]
    assert choose_validated_first(ORDER, validations) == ORDER


def test_multiple_comparison_control_blocks_weak_p_value():
    validations = [
        _row("smart_media", "legacy_extractor", p=0.04),
        _row("smart_media", "browser_media", p=0.04),
        _row("smart_media", "cobalt", p=0.04),
    ]
    assert choose_validated_first(ORDER, validations) == ORDER


def test_missing_peer_comparison_fails_closed():
    validations = [
        _row("smart_media", "legacy_extractor"),
        _row("smart_media", "browser_media"),
    ]
    assert choose_validated_first(ORDER, validations) == ORDER


def test_nonpositive_confidence_bound_fails_closed():
    validations = [
        _row("smart_media", "legacy_extractor", lower=0.0),
        _row("smart_media", "browser_media"),
        _row("smart_media", "cobalt"),
    ]
    assert choose_validated_first(ORDER, validations) == ORDER


def test_invalid_configuration_fails_closed():
    validations = [
        _row("smart_media", "legacy_extractor"),
        _row("smart_media", "browser_media"),
        _row("smart_media", "cobalt"),
    ]
    assert choose_validated_first(ORDER, validations, alpha=1.0) == ORDER


def test_existing_first_resolver_remains_unchanged_when_it_wins():
    validations = [
        _row("legacy_extractor", "smart_media"),
        _row("legacy_extractor", "browser_media"),
        _row("legacy_extractor", "cobalt"),
    ]
    assert choose_validated_first(ORDER, validations) == ORDER


def test_unknown_resolvers_are_never_added_or_selected():
    validations = [
        _row("unknown", "smart_media"),
        _row("unknown", "legacy_extractor"),
    ]
    assert choose_validated_first(ORDER, validations) == ORDER
