from downloader.resolver_outcome_telemetry import ResolverOutcomeTelemetry


def test_resolver_outcome_telemetry_records_and_summarizes(tmp_path):
    telemetry = ResolverOutcomeTelemetry(tmp_path / "smart_learning.db")

    telemetry.record("smart_media", success=True, candidate_count=3, elapsed_ms=12.5)
    telemetry.record("smart_media", success=False, candidate_count=0, elapsed_ms=7.5, failure_reason="TimeoutError")
    telemetry.record("browser_media", success=True, candidate_count=1, elapsed_ms=20.0)

    summary = {row["resolver"]: row for row in telemetry.summary()}

    assert summary["smart_media"]["attempts"] == 2
    assert summary["smart_media"]["successes"] == 1
    assert summary["smart_media"]["success_rate"] == 0.5
    assert summary["browser_media"]["attempts"] == 1
    assert summary["browser_media"]["success_rate"] == 1.0


def test_resolver_outcome_telemetry_bounds_values(tmp_path):
    telemetry = ResolverOutcomeTelemetry(tmp_path / "smart_learning.db")
    telemetry.record("r" * 200, success=True, candidate_count=-4, elapsed_ms=-10, failure_reason="x" * 1000)

    row = telemetry.summary()[0]
    assert len(row["resolver"]) == 80
    assert row["attempts"] == 1
    assert row["successes"] == 1


def test_resolver_policy_requires_minimum_sample_and_ranks_safely(tmp_path):
    telemetry = ResolverOutcomeTelemetry(tmp_path / "smart_learning.db")

    for _ in range(20):
        telemetry.record("browser_media", success=True, elapsed_ms=40)
        telemetry.record("smart_media", success=True, elapsed_ms=10)
        telemetry.record("cobalt", success=False, elapsed_ms=5)
    telemetry.record("noisy", success=True, elapsed_ms=1)

    policy = telemetry.resolver_policy(min_attempts=20)
    names = [row["resolver"] for row in policy]

    assert "noisy" not in names
    assert names[:3] == ["browser_media", "smart_media", "cobalt"]
    assert policy[0]["success_rate"] == 1.0
    assert policy[1]["success_rate"] == 1.0
    assert policy[0]["avg_elapsed_ms"] > policy[1]["avg_elapsed_ms"]


def test_resolver_policy_is_bounded_and_clamps_arguments(tmp_path):
    telemetry = ResolverOutcomeTelemetry(tmp_path / "smart_learning.db")
    for index in range(20):
        telemetry.record(f"resolver-{index}", success=True, elapsed_ms=index + 1)

    policy = telemetry.resolver_policy(min_attempts=0, max_resolvers=999)
    assert len(policy) == 16
    assert policy[0]["resolver"] == "resolver-0"
