from downloader.resolver_outcome_telemetry import ResolverOutcomeTelemetry


def test_platform_policy_combines_media_kinds(tmp_path):
    telemetry = ResolverOutcomeTelemetry(tmp_path / "smart_learning.db")
    for _ in range(20):
        telemetry.record("legacy_extractor", success=False, platform="youtube", media_kind="progressive")
        telemetry.record("smart_media", success=True, platform="youtube", media_kind="hls")
    telemetry.record("smart_media", success=True, platform="instagram", media_kind="hls")

    policy = telemetry.platform_policy(platform="youtube", min_attempts=20)
    names = [row["resolver"] for row in policy]
    assert names[:2] == ["smart_media", "legacy_extractor"]
