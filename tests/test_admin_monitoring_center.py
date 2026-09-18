import json
import sqlite3
from pathlib import Path

from plugins.admin_monitoring_center import (
    _counts,
    _refresh_alerts,
    _render_alerts,
    _render_incidents,
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
