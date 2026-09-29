import json
import sqlite3
from pathlib import Path

from plugins.admin_monitoring_center import (
    _counts,
    _refresh_alerts,
    _render_alerts,
    _render_last_error,
    _render_last_error_compact,
    _last_error_keyboard,
    _render_incidents,
    _home_keyboard,
    _incident_keyboard,
    _render_resolvers,
    _resolver,
    ensure_schema,
)


def _db_factory(path: Path):
    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    return get_db


def _setup(path: Path):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT,
            website TEXT,
            media_type TEXT,
            stage TEXT,
            error_type TEXT,
            error_message TEXT,
            attempt_id TEXT,
            attempt_number INTEGER,
            http_status INTEGER,
            details_json TEXT,
            created_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def test_monitoring_schema_and_alert_aggregation(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)
    ensure_schema(get_db)

    conn = get_db()
    for i in range(3):
        conn.execute(
            """INSERT INTO error_logs
               (user_id,url,website,media_type,stage,error_type,error_message,
                attempt_id,attempt_number,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                1,
                "https://www.instagram.com/reel/ABC123/",
                "instagram",
                "video",
                "download",
                "all_methods_failed",
                "All available download methods failed.",
                f"attempt-{i}",
                i + 1,
                json.dumps({"fallback": {"resolver": "instagram_relay_html"}}),
            ),
        )
    conn.commit()
    conn.close()

    assert _refresh_alerts(get_db) == 1
    counts = _counts(get_db)
    assert counts["open_count"] == 1
    assert counts["critical"] == 1

    incident_text = _render_incidents(get_db)
    assert "Error & Incident Center" in incident_text
    assert "instagram" in incident_text

    alert_text, _ = _render_alerts(get_db)
    assert "Admin Alerts" in alert_text
    assert "all_methods_failed" in alert_text


def test_resolver_monitor_uses_distinct_attempts(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)

    conn = get_db()
    details = json.dumps({"fallback": {"resolver": "instagram_relay_html"}})
    for attempt in ("a1", "a1", "a2"):
        conn.execute(
            """INSERT INTO error_logs
               (url,website,media_type,stage,error_type,error_message,
                attempt_id,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                "https://www.instagram.com/reel/ABC123/",
                "instagram",
                "video",
                "download",
                "resolver_failed",
                "resolver failed",
                attempt,
                details,
            ),
        )
    conn.commit()
    conn.close()

    text = _render_resolvers(get_db)
    assert "instagram" in text
    assert "instagram_relay_html" in text
    assert "محاولات متأثرة: <b>2</b>" in text


def test_resolver_prefers_nested_resolver_name():
    class Row(dict):
        def keys(self):
            return super().keys()

        def __getitem__(self, key):
            return super().__getitem__(key)

    row = Row({
        "details_json": json.dumps({"outer": {"resolver_name": "instagram_graphql"}}),
        "stage": "download",
    })
    assert _resolver(row) == "instagram_graphql"


def test_intermediate_resolver_failures_do_not_create_admin_alert(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)
    ensure_schema(get_db)

    conn = get_db()
    for stage, error_type in (
        ("yt-dlp", "yt_dlp_failed"),
        ("yoinku", "yoinku_failed"),
    ):
        conn.execute(
            """INSERT INTO error_logs
               (url,website,media_type,stage,error_type,error_message,
                attempt_id,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                "https://www.tiktok.com/@demo/video/123456",
                "tiktok",
                "video",
                stage,
                error_type,
                f"{error_type} during fallback chain",
                "successful-attempt",
                json.dumps({"resolver": stage}),
            ),
        )
    conn.commit()
    conn.close()

    assert _refresh_alerts(get_db) == 0
    assert _counts(get_db)["open_count"] == 0
    assert _render_alerts(get_db)[0].count("tiktok") == 0


def test_terminal_failure_collapses_attempt_to_one_admin_alert(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)
    ensure_schema(get_db)

    conn = get_db()
    for stage, error_type in (
        ("yt-dlp", "yt_dlp_failed"),
        ("yoinku", "yoinku_failed"),
        ("direct_fallback", "fallback_failed"),
        ("download", "all_methods_failed"),
    ):
        conn.execute(
            """INSERT INTO error_logs
               (url,website,media_type,stage,error_type,error_message,
                attempt_id,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                "https://www.tiktok.com/@demo/video/123456",
                "tiktok",
                "video",
                stage,
                error_type,
                f"{error_type} for the same attempt",
                "failed-attempt",
                json.dumps({"resolver": stage}),
            ),
        )
    conn.commit()
    conn.close()

    assert _refresh_alerts(get_db) == 1
    counts = _counts(get_db)
    assert counts["open_count"] == 1
    assert counts["critical"] == 1

    conn = get_db()
    row = conn.execute("SELECT title, resolver, event_count FROM admin_alerts").fetchone()
    conn.close()

    assert row["title"] == "tiktok / download — all_methods_failed"
    assert row["resolver"] == "download"
    assert row["event_count"] == 1


def test_previous_intermediate_alerts_are_converged_to_resolved(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)
    ensure_schema(get_db)

    conn = get_db()
    conn.execute(
        """INSERT INTO admin_alerts
           (fingerprint,category,severity,status,title,details,platform,resolver,
            event_count,first_seen,last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-fp",
            "incident",
            "medium",
            "open",
            "tiktok / yoinku — yoinku_failed",
            "legacy intermediate failure",
            "tiktok",
            "yoinku",
            2,
            "2026-09-27T20:00:00",
            "2026-09-27T20:01:00",
        ),
    )
    conn.execute(
        """INSERT INTO error_logs
           (url,website,media_type,stage,error_type,error_message,
            attempt_id,details_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
        (
            "https://www.tiktok.com/@demo/video/123456",
            "tiktok",
            "video",
            "yt-dlp",
            "yt_dlp_failed",
            "primary failed but fallback succeeded",
            "successful-attempt",
            json.dumps({"resolver": "yt-dlp"}),
        ),
    )
    conn.commit()
    conn.close()

    assert _refresh_alerts(get_db) == 0
    conn = get_db()
    row = conn.execute(
        "SELECT status FROM admin_alerts WHERE fingerprint='legacy-fp'"
    ).fetchone()
    conn.close()
    assert row["status"] == "resolved"


def _callback_values(keyboard):
    return [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]


def test_monitoring_home_compacts_duplicate_alert_navigation():
    callbacks = _callback_values(_home_keyboard())
    assert callbacks.count("admin_alerts") == 1
    assert "admin_incidents" not in callbacks
    assert "admin_ops_resolvers" not in callbacks
    assert "admin_ops_timeline" not in callbacks
    assert "admin_observability" in callbacks
    assert "admin_resolver_monitor" in callbacks
    assert len(callbacks) == 5


def test_legacy_incident_keyboard_no_longer_duplicates_alert_entry():
    callbacks = _callback_values(_incident_keyboard())
    assert callbacks.count("admin_alerts") == 1
    assert "admin_resolver_monitor" in callbacks
    assert "admin_home" in callbacks



def test_last_error_snapshot_contains_all_available_fields_and_redacts_secrets(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)

    conn = get_db()
    conn.execute(
        """INSERT INTO error_logs
           (user_id,url,website,media_type,stage,error_type,error_message,
            attempt_id,attempt_number,http_status,details_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            7,
            "https://example.com/video?id=123",
            "example",
            "video",
            "download",
            "runtime_error",
            "connection failed",
            "attempt-last",
            3,
            502,
            json.dumps({
                "resolver": "example_resolver",
                "traceback": "Traceback (most recent call last):\nRuntimeError: boom",
                "authorization": "Bearer SUPER_SECRET",
                "nested": {"duration_ms": 1234},
            }),
            "2026-09-28T04:20:00",
        ),
    )
    conn.commit()
    conn.close()

    chunks = _render_last_error(get_db)
    snapshot = "\n".join(chunks)

    assert "attempt-last" in snapshot
    assert "connection failed" in snapshot
    assert "example_resolver" in snapshot
    assert "RuntimeError: boom" in snapshot
    assert "duration_ms" in snapshot
    assert "[REDACTED]" in snapshot
    assert "SUPER_SECRET" not in snapshot

    assert "=== INCIDENT SUMMARY ===" in snapshot
    assert "record_source" in snapshot
    assert "phase" in snapshot
    assert "=== ERROR_LOG ROW ===" in snapshot


def test_last_error_snapshot_preserves_nested_diagnostic_evidence(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)

    conn = get_db()
    details = {
        "pipeline_phase": "candidate_download",
        "resolver": "yt-dlp",
        "candidate_count": 8,
        "candidates": [
            {
                "candidate_index": 1,
                "format_id": "720p",
                "status": "failed",
                "return_code": 1,
                "duration_ms": 512,
                "error_message": "generic download failure",
            }
        ],
        "fallback": {
            "provider": "yoinku",
            "status": "failed",
            "http_status": 422,
            "internal_attempts": 3,
        },
    }
    conn.execute(
        """INSERT INTO error_logs
           (url,website,media_type,stage,error_type,error_message,
            attempt_id,attempt_number,http_status,details_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "https://example.com/reel/abc",
            "example",
            "video",
            "download",
            "all_methods_failed",
            "all methods failed",
            "attempt-evidence",
            1,
            422,
            json.dumps(details),
            "2026-09-28T04:30:00",
        ),
    )
    conn.commit()
    conn.close()

    snapshot = "\n".join(_render_last_error(get_db))
    for value in (
        "candidate_download",
        "candidate_count",
        "720p",
        "return_code",
        "generic download failure",
        "yoinku",
        "http_status",
        "422",
        "internal_attempts",
    ):
        assert value in snapshot



def test_last_error_snapshot_includes_correlated_attempt_timeline(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)

    conn = get_db()
    rows = [
        (
            "yt-dlp",
            "yt_dlp_failed",
            "Sign in to confirm you are not a bot",
            None,
            json.dumps({
                "resolver": "yt-dlp",
                "candidate_count": 0,
                "diagnostics": ["resolution_failed:bot_check"],
            }),
        ),
        (
            "yoinku",
            "yoinku_failed",
            "HTTP Error 422: Unprocessable Entity",
            422,
            json.dumps({
                "provider": "yoinku",
                "format_id": "v-720",
                "internal_attempts": 3,
            }),
        ),
        (
            "download",
            "all_methods_failed",
            "All available download methods failed.",
            None,
            json.dumps({
                "fallback": {
                    "candidate_count": "0",
                    "skipped": "youtube_direct_fallback_not_applicable",
                }
            }),
        ),
    ]
    for index, (stage, error_type, message, http_status, details) in enumerate(rows, start=1):
        conn.execute(
            """INSERT INTO error_logs
               (user_id,url,website,media_type,stage,error_type,error_message,
                attempt_id,attempt_number,http_status,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                7,
                "https://www.youtube.com/watch?v=example",
                "YouTube",
                "video",
                stage,
                error_type,
                message,
                "same-attempt",
                1,
                http_status,
                details,
                f"2026-09-28T05:0{index}:00",
            ),
        )
    conn.commit()
    conn.close()

    snapshot = "\n".join(_render_last_error(get_db))

    assert "=== ATTEMPT TIMELINE / CORRELATED EVIDENCE ===" in snapshot
    assert "events_in_same_attempt: 3" in snapshot
    assert "yt_dlp_failed" in snapshot
    assert "bot_check" in snapshot
    assert "yoinku_failed" in snapshot
    assert "422" in snapshot
    assert "internal_attempts" in snapshot
    assert "all_methods_failed" in snapshot
    assert "youtube_direct_fallback_not_applicable" in snapshot



def test_last_error_snapshot_ignores_newer_intermediate_failure_after_terminal_incident(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)

    conn = get_db()
    rows = [
        ("download", "all_methods_failed", "terminal failure", "terminal-1", "2026-09-28T06:00:00"),
        ("yt-dlp", "yt_dlp_failed", "intermediate diagnostic", "terminal-1", "2026-09-28T06:01:00"),
    ]
    for stage, error_type, message, attempt_id, created_at in rows:
        conn.execute(
            """INSERT INTO error_logs
               (url,website,media_type,stage,error_type,error_message,
                attempt_id,attempt_number,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "https://example.com/video",
                "YouTube",
                "video",
                stage,
                error_type,
                message,
                attempt_id,
                1,
                "{}",
                created_at,
            ),
        )
    conn.commit()
    conn.close()

    snapshot = "\n".join(_render_last_error(get_db))
    assert "terminal failure" in snapshot
    assert "error_type: all_methods_failed" in snapshot
    assert "error_message: terminal failure" in snapshot
    assert "آخر حادث فشل نهائي" in snapshot


def test_last_error_keyboard_exposes_refresh_and_navigation():
    callbacks = _callback_values(_last_error_keyboard())
    assert callbacks[0] == "admin_last_error"
    assert "admin_last_error_copy" in callbacks
    assert "admin_last_error_compact" in callbacks
    assert "admin_alerts" in callbacks
    assert "admin_resolver_monitor" in callbacks


def test_last_error_keyboard_exposes_copy_action_before_refresh():
    callbacks = _callback_values(_last_error_keyboard())
    assert callbacks[0] == "admin_last_error"
    assert callbacks[1] == "admin_last_error_copy"
    assert callbacks[2] == "admin_last_error_compact"



def test_last_error_compact_snapshot_has_cyber_incident_structure_and_redaction(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)
    conn = get_db()
    rows = [
        ("yt-dlp", "yt_dlp_failed", "bot check", None,
         json.dumps({"authorization": "Bearer SECRET", "resolver": "yt-dlp"})),
        ("yoinku", "yoinku_failed", "HTTP Error 422: Unprocessable Entity", 422,
         json.dumps({"format_id": "v-720"})),
        ("download", "all_methods_failed", "All available download methods failed.", None,
         json.dumps({"fallback": {"skipped": "youtube_direct_fallback_not_applicable"}})),
    ]
    for index, (stage, error_type, message, status, details) in enumerate(rows, start=1):
        conn.execute(
            """INSERT INTO error_logs
               (url,website,media_type,stage,error_type,error_message,
                attempt_id,attempt_number,http_status,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("https://www.youtube.com/watch?v=example", "YouTube", "video",
             stage, error_type, message, "cyber-attempt", 1, status, details,
             f"2026-09-29T08:0{index}:00"),
        )
    conn.commit()
    conn.close()

    snapshot = _render_last_error_compact(get_db)
    assert "CYBER INCIDENT SNAPSHOT" in snapshot
    assert "DOWNLOAD_PIPELINE_FAILURE" in snapshot
    assert "TERMINAL_FAILURE" in snapshot
    assert "affected_platform" in snapshot
    assert "security_controls" in snapshot
    assert "credentials_redacted" in snapshot
    assert "correlated_evidence" in snapshot
    assert "yt_dlp_failed" in snapshot
    assert "yoinku_failed" in snapshot
    assert "422" in snapshot
    assert "all_methods_failed" in snapshot
    assert "[REDACTED]" in snapshot
    assert "Bearer SECRET" not in snapshot

def test_studio_error_snapshot_is_operation_scoped(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)
    conn = get_db()
    conn.execute(
        """INSERT INTO error_logs
           (url,website,media_type,stage,error_type,error_message,attempt_id,details_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("", "YouTube", "video", "download", "all_methods_failed",
         "old youtube failure", "download-attempt",
         json.dumps({"resolver":"yt-dlp"}), "2026-09-28T06:00:00"),
    )
    conn.execute(
        """INSERT INTO error_logs
           (url,website,media_type,stage,error_type,error_message,attempt_id,details_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("", "AliBot Studio", "audio", "media_studio", "media_studio_failed",
         "studio failure", "studio-attempt",
         json.dumps({
             "operation":"media_studio",
             "studio_attempt_id":"studio-attempt",
             "source_token":"source-token",
             "studio_token":"studio-token",
             "studio_action":"audio",
             "media_type":"audio",
             "ffmpeg_stderr_tail":"codec error",
         }), "2026-09-28T07:00:00"),
    )
    conn.commit()
    conn.close()
    from plugins.admin_monitoring_center import _render_studio_error
    snapshot = "\n".join(_render_studio_error(get_db))
    assert "media_studio_failed" in snapshot
    assert "studio-attempt" in snapshot
    assert "audio" in snapshot
    assert "codec error" in snapshot
    assert "old youtube failure" not in snapshot


def test_last_error_snapshot_marks_newer_failure_telemetry_as_stale_context(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)
    conn = get_db()
    rows = [
        ("download", "all_methods_failed", "terminal failure", "old-attempt", "2026-09-28T06:00:00"),
        ("yt-dlp", "yt_dlp_failed", "newer failure telemetry", "new-attempt", "2026-09-28T07:00:00"),
    ]
    for stage, error_type, message, attempt_id, created_at in rows:
        conn.execute(
            """INSERT INTO error_logs
               (url,website,media_type,stage,error_type,error_message,
                attempt_id,attempt_number,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("https://example.com/video", "YouTube", "video", stage, error_type,
             message, attempt_id, 1, "{}", created_at),
        )
    conn.commit()
    conn.close()

    snapshot = "\n".join(_render_last_error(get_db))
    assert "توجد telemetry فشل أحدث من الحادث المعروض" in snapshot
    assert "2026-09-28T07:00:00" in snapshot
    assert "newer telemetry" not in snapshot


def test_last_error_snapshot_marks_same_attempt_as_current_context(tmp_path):
    path = tmp_path / "bot.db"
    _setup(path)
    get_db = _db_factory(path)
    conn = get_db()
    rows = [
        ("yt-dlp", "yt_dlp_failed", "intermediate failure", "same-attempt", "2026-09-28T06:00:00"),
        ("download", "all_methods_failed", "terminal failure", "same-attempt", "2026-09-28T06:01:00"),
    ]
    for stage, error_type, message, attempt_id, created_at in rows:
        conn.execute(
            """INSERT INTO error_logs
               (url,website,media_type,stage,error_type,error_message,
                attempt_id,attempt_number,details_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("https://example.com/video", "YouTube", "video", stage, error_type,
             message, attempt_id, 1, "{}", created_at),
        )
    conn.commit()
    conn.close()

    snapshot = "\n".join(_render_last_error(get_db))
    assert "نفس محاولة الحادث" in snapshot
    assert "2026-09-28T06:01:00" in snapshot
