from __future__ import annotations

import time
from pathlib import Path

from download_events import DownloadEvent
from downloader.resolver_budget import ResolverBudget
from downloader.resolver_contracts import ResolverResult
from storage_lifecycle import cleanup_transient_storage


def test_resolver_result_normalizes_legacy_outputs():
    result = ResolverResult.from_output("legacy", ["a", "b"], elapsed_ms=12)
    assert result.status == "success"
    assert result.candidate_count == 2
    assert result.ok
    empty = ResolverResult.from_output("legacy", [])
    assert empty.status == "empty"
    assert not empty.ok


def test_resolver_budget_caps_requested_limits(monkeypatch):
    monkeypatch.setenv("ALIBOT_RESOLVER_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("ALIBOT_VALIDATION_TIMEOUT_SECONDS", "4")
    budget = ResolverBudget.from_environment()
    assert budget.apply(timeout=90, validation_timeout=30, max_html_bytes=20 * 1024 * 1024, max_ranked_candidates=200) == (10.0, 4.0, 5 * 1024 * 1024, 100)


def test_download_event_requires_successful_delivery():
    event = DownloadEvent(user_id=1, username="ali", url="https://example.com/video", website="example", media_type="video", quality="720p")
    assert event.delivery_status == "delivered"
    assert event.delivered_parts == 1
    assert event.timestamp


def test_transient_cleanup_refuses_non_canonical_root(tmp_path):
    result = cleanup_transient_storage(root=tmp_path)
    assert result["skipped"] == 1
    assert list(tmp_path.iterdir()) == []


def test_transient_cleanup_removes_old_entries(monkeypatch, tmp_path):
    import storage_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "DEFAULT_TMP_ROOT", tmp_path.resolve())
    old_file = tmp_path / "old.tmp"
    old_file.write_text("x", encoding="utf-8")
    old_time = time.time() - 13 * 60 * 60
    old_file.touch()
    import os
    os.utime(old_file, (old_time, old_time))
    fresh = tmp_path / "fresh.tmp"
    fresh.write_text("keep", encoding="utf-8")
    result = lifecycle.cleanup_transient_storage(root=tmp_path)
    assert result["removed_files"] == 1
    assert not old_file.exists()
    assert fresh.exists()


def test_gemini_isolated_from_bot_initialization():
    bot_source = Path(__file__).resolve().parents[1].joinpath("bot.py").read_text(encoding="utf-8")
    assert "from google import genai" not in bot_source
    assert "from plugins import gemini_service" in bot_source
