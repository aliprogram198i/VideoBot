import sqlite3

from plugins.admin_operations_observability import (
    _groups,
    _storage_data,
    _timeline_rows,
    _timeline_text,
)


def _db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE downloads (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            website TEXT,
            media_type TEXT,
            quality TEXT,
            title TEXT,
            created_at TEXT
        );
        CREATE TABLE error_logs (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            url TEXT,
            website TEXT,
            media_type TEXT,
            stage TEXT,
            error_type TEXT,
            error_message TEXT,
            attempt_id TEXT,
            attempt_number INTEGER,
            details_json TEXT,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO downloads VALUES (1, 7, 'instagram', 'video', '720p', 'ok', '2026-09-25T09:00:00')"
    )
    conn.execute(
        "INSERT INTO error_logs VALUES "
        '(1,7,\'https://instagram.com/reel/x\',\'instagram\',\'video\',\'download\',\'resolver_failed\',\'failed\',\'a1\',1,\'{"fallback":{"resolver":"instagram_relay_html"}}\',\'2026-09-25T08:59:00\')'
    )
    conn.execute(
        "INSERT INTO error_logs VALUES "
        '(2,7,\'https://instagram.com/reel/x\',\'instagram\',\'video\',\'download\',\'resolver_failed\',\'failed\',\'a1\',2,\'{"fallback":{"resolver":"instagram_relay_html"}}\',\'2026-09-25T08:58:00\')'
    )
    conn.commit()
    return conn


def test_timeline_merges_success_and_error_events(tmp_path):
    path = tmp_path / "bot.db"
    _db(path).close()

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    events = _timeline_rows(get_db)
    assert [event["kind"] for event in events] == ["download", "error", "error"]
    assert "Delivery SUCCESS" in _timeline_text(events)


def test_resolver_groups_distinct_attempt_ids(tmp_path):
    path = tmp_path / "bot.db"
    _db(path).close()

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    groups = _groups(get_db)
    assert len(groups) == 1
    assert groups[0]["events"] == 2
    assert len(groups[0]["attempts"]) == 1
    assert groups[0]["resolver"] == "instagram_relay_html"


def test_storage_monitor_is_read_only():
    rows = _storage_data()
    assert rows
    assert all("free" in row and "total" in row for row in rows)


def test_admin_navigation_uses_unified_monitoring_path():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    command_center = (root / "plugins" / "admin_control_center.py").read_text(encoding="utf-8")
    operations = (root / "plugins" / "admin_operations_center.py").read_text(encoding="utf-8")
    monitoring = (root / "plugins" / "admin_monitoring_center.py").read_text(encoding="utf-8")

    assert 'callback_data="admin_monitoring"' in command_center
    assert 'callback_data="admin_incidents"' not in command_center
    assert 'callback_data="admin_resolver_monitor"' not in command_center
    assert 'callback_data="admin_alerts"' not in command_center
    assert 'callback_data="admin_monitoring"' in operations
    assert 'callback_data="admin_ops_resolvers"' in monitoring
    assert 'callback_data="admin_ops_timeline"' in monitoring
