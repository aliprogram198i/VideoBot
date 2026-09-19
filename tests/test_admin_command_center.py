import sqlite3

from plugins.admin_command_center import command_center_keyboard
from plugins.admin_event_model import recent_events, summary


def _db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE downloads (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            website TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE error_logs (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            website TEXT,
            error_type TEXT,
            error_message TEXT,
            created_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO downloads VALUES (1, 7, 'instagram', '2026-09-18T10:00:00')"
    )
    conn.execute(
        "INSERT INTO error_logs VALUES (2, 7, 'instagram', 'resolver_failed', 'failed', '2026-09-18T10:01:00')"
    )
    conn.commit()
    conn.close()

    def get_db():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db

    return get_db


def test_command_center_has_all_current_domains_and_more():
    rows = command_center_keyboard().inline_keyboard
    callbacks = [button.callback_data for row in rows for button in row]
    assert callbacks == [
        "admin_users_page_0",
        "admin_records",
        "admin_ops_dashboard",
        "admin_health",
        "admin_smart_operations",
        "admin_group_publisher",
        "admin_broadcast",
        "admin_roles",
        "admin_more",
    ]


def test_unified_event_adapter_is_read_only(tmp_path):
    get_db = _db(tmp_path / "admin.sqlite")
    before = get_db().execute("SELECT COUNT(*) FROM downloads").fetchone()[0]

    events = recent_events(get_db)
    data = summary(get_db)

    assert data == {"downloads": 1, "errors": 1}
    assert [item["kind"] for item in events] == ["error", "download"]
    assert events[0]["platform"] == "instagram"
    assert get_db().execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == before
