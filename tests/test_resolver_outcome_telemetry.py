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
