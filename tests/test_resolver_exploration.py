import random

from downloader.resolver_exploration import exploratory_order, normalized_rate, should_explore


def test_exploration_is_disabled_by_default():
    assert normalized_rate(None) == 0.0
    assert normalized_rate("invalid") == 0.0
    assert should_explore(None, draw=0.0) is False


def test_exploration_rate_is_strictly_bounded():
    assert normalized_rate(-1) == 0.0
    assert normalized_rate(0.02) == 0.02
    assert normalized_rate(1.0) == 0.05


def test_exploration_decision_is_deterministic_for_supplied_draw():
    assert should_explore(0.02, draw=0.019) is True
    assert should_explore(0.02, draw=0.02) is False
    assert should_explore(0.02, draw=0.5) is False
    assert should_explore(0.02, draw=1.0) is False


def test_exploration_order_preserves_all_resolvers():
    original = ["legacy_extractor", "smart_media", "browser_media", "cobalt"]
    result = exploratory_order(original, rng=random.Random(7))
    assert sorted(result) == sorted(original)
    assert len(result) == len(original)


def test_exploration_order_fails_closed_for_invalid_input():
    assert exploratory_order([]) == []
    duplicate = ["legacy_extractor", "legacy_extractor"]
    assert exploratory_order(duplicate) == duplicate
