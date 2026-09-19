import asyncio

from download_lifecycle import DownloadLifecycle, DownloadState


def test_lifecycle_has_correlation_ids_and_transitions():
    lifecycle = DownloadLifecycle(1, "https://example.com/v", "Example", "video")
    assert lifecycle.job_id
    assert lifecycle.attempt_id
    lifecycle.transition(DownloadState.RESOLVING)
    assert lifecycle.state is DownloadState.RESOLVING
