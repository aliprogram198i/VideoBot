from __future__ import annotations

import time
from pathlib import Path

from download_events import DownloadEvent
from data_layer import record_download
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


def test_download_event_supports_delivery_and_terminal_failure():
    delivered = DownloadEvent(
        user_id=1,
        username="ali",
        url="https://example.com/image",
        website="Instagram",
        media_type="image",
        quality="Original",
        attempt_id="attempt-1",
        attempt_number=1,
        delivered_parts=1,
        elapsed_ms=123.4,
    )
    assert delivered.success
    assert delivered.ledger_eligible
    assert delivered.media_type == "image"

    failed = DownloadEvent.failed(
        user_id=1,
        url="https://example.com/video",
        website="YouTube",
        media_type="video",
        quality="720p",
        attempt_id="attempt-2",
        attempt_number=1,
        elapsed_ms=456.7,
        failure_reason="YtDlpProcessError",
    )
    assert not failed.success
    assert not failed.ledger_eligible
    assert failed.delivered_parts == 0


def test_failed_download_event_cannot_enter_ledger(tmp_path, monkeypatch):
    import sqlite3
    import data_layer

    db = tmp_path / "ledger.db"
    monkeypatch.setattr(data_layer, "DB_FILE", str(db))
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, downloads INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, url TEXT, website TEXT, media_type TEXT, quality TEXT, created_at TEXT)")
    conn.execute("INSERT INTO users(user_id, downloads) VALUES(1, 0)")
    conn.commit()
    conn.close()

    event = DownloadEvent.failed(
        user_id=1,
        url="https://example.com/video",
        website="YouTube",
        media_type="video",
        quality="720p",
        failure_reason="TerminalFailure",
    )

    try:
        record_download(event=event)
    except ValueError as exc:
        assert "successfully delivered" in str(exc)
    else:
        raise AssertionError("failed event was accepted by the ledger")


def test_bot_wires_terminal_download_outcome_to_canonical_event():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "from download_events import DownloadEvent" in source
    assert "from downloader.telemetry import TelemetryRecorder" in source
    assert "delivery_confirmed = True" in source
    assert "record_download_failure(" in source
    assert "if not delivery_confirmed:" in source
    assert "attempt_id=attempt_id" in source
    assert "delivered_parts=total_parts" in source
