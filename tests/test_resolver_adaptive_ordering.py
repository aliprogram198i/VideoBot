from downloader.resolver_adaptive_selector import order_resolvers


def test_strong_winner_moves_first_without_losing_entries():
    order = ["legacy_extractor", "smart_media", "browser_media", "cobalt"]
    policy = [
        {"resolver": "legacy_extractor", "attempts": 20, "success_rate": 0.50},
        {"resolver": "smart_media", "attempts": 20, "success_rate": 0.80},
    ]
    result = order_resolvers(order, policy)
    assert result == ["smart_media", "legacy_extractor", "browser_media", "cobalt"]


def test_weak_or_incomplete_evidence_keeps_original_order():
    order = ["legacy_extractor", "smart_media", "browser_media", "cobalt"]
    weak = [
        {"resolver": "legacy_extractor", "attempts": 20, "success_rate": 0.70},
        {"resolver": "smart_media", "attempts": 20, "success_rate": 0.75},
    ]
    incomplete = [
        {"resolver": "legacy_extractor", "attempts": 20, "success_rate": 0.50},
        {"resolver": "browser_media", "attempts": 19, "success_rate": 1.00},
    ]
    assert order_resolvers(order, weak) == order
    assert order_resolvers(order, incomplete) == order
