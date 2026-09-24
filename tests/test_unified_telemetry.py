"""Tests for the unified telemetry facade."""

from __future__ import annotations

import tempfile
import unittest

from downloader.resolver_contracts import ResolverResult
from downloader.telemetry import TelemetryContext, TelemetryRecorder


class UnifiedTelemetryTests(unittest.TestCase):
    def test_resolver_and_download_events_share_one_store(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            db = f"{root}/telemetry.db"
            telemetry = TelemetryRecorder(db)

            telemetry.record_resolver(
                ResolverResult(
                    resolver="test_resolver",
                    status="success",
                    candidates=("candidate",),
                    elapsed_ms=12.5,
                ),
                context=TelemetryContext(platform="youtube", media_kind="progressive"),
            )
            telemetry.record_download(
                platform="youtube",
                media_type="video",
                success=True,
                elapsed_ms=55.0,
                url="https://example.test/video.mp4?secret=should-not-be-stored",
                attempt_id="attempt-1",
            )
            telemetry.record_download(
                platform="youtube",
                media_type="video",
                success=False,
                elapsed_ms=30.0,
                url="https://example.test/video.mp4",
                failure_reason="DeliveryFailed",
                attempt_id="attempt-2",
            )

            summary = telemetry.download_summary()
            self.assertEqual(len(summary), 1)
            self.assertEqual(summary[0]["attempts"], 2)
            self.assertEqual(summary[0]["successes"], 1)

            resolver_summary = telemetry._resolver.summary()
            self.assertEqual(resolver_summary[0]["resolver"], "test_resolver")
            self.assertEqual(resolver_summary[0]["successes"], 1)

            with open(db, "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"secret=should-not-be-stored", raw)


if __name__ == "__main__":
    unittest.main()
