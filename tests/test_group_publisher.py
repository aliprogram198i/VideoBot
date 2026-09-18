import sqlite3

from plugins.group_publisher import ensure_schema, _upsert_group, _list_groups, _delete_group


class Owner:
    def __init__(self, user_id, username=None):
        self.id = user_id
        self.username = username


def make_db(tmp_path):
    path = tmp_path / "bot.db"

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return get_db


def test_group_schema_is_idempotent(tmp_path):
    get_db = make_db(tmp_path)
    ensure_schema(get_db)
    ensure_schema(get_db)
    conn = get_db()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(bot_groups)")}
    conn.close()
    assert {"chat_id", "owner_user_id", "created_at", "updated_at"} <= columns


def test_group_owner_is_preserved_on_refresh(tmp_path):
    get_db = make_db(tmp_path)
    ensure_schema(get_db)
    _upsert_group(get_db, -1001, "Test", Owner(11, "alice"))
    _upsert_group(get_db, -1001, "Renamed", Owner(22, "other"))
    rows = _list_groups(get_db, 11)
    assert len(rows) == 1
    assert rows[0]["title"] == "Renamed"
    assert not _list_groups(get_db, 22)


def test_only_owner_can_remove(tmp_path):
    get_db = make_db(tmp_path)
    ensure_schema(get_db)
    _upsert_group(get_db, -1001, "Test", Owner(11))
    assert not _delete_group(get_db, -1001, 22)
    assert _delete_group(get_db, -1001, 11)


def test_publish_record_updates_last_publish_and_count(tmp_path):
    from plugins.group_publisher import _record_publish

    get_db = make_db(tmp_path)
    ensure_schema(get_db)
    _upsert_group(get_db, -1001, "Test", Owner(11))
    _record_publish(get_db, -1001, 11, "owner", "hello", "success")

    conn = get_db()
    row = conn.execute(
        "SELECT status,last_publish_at,publish_count FROM bot_groups WHERE chat_id=?",
        (-1001,),
    ).fetchone()
    log = conn.execute(
        "SELECT actor_type,status,message_preview FROM group_publish_logs WHERE chat_id=?",
        (-1001,),
    ).fetchone()
    conn.close()

    assert row["status"] == "active"
    assert row["last_publish_at"]
    assert row["publish_count"] == 1
    assert log["actor_type"] == "owner"
    assert log["status"] == "success"
    assert log["message_preview"] == "hello"


def test_failed_publish_records_error_and_group_state(tmp_path):
    from plugins.group_publisher import _record_publish

    get_db = make_db(tmp_path)
    ensure_schema(get_db)
    _upsert_group(get_db, -1001, "Test", Owner(11))
    _record_publish(get_db, -1001, 1486412391, "admin", "hello", "failed", "BotPermissionError")

    conn = get_db()
    row = conn.execute(
        "SELECT status,publish_count,last_publish_at FROM bot_groups WHERE chat_id=?",
        (-1001,),
    ).fetchone()
    log = conn.execute(
        "SELECT actor_type,status,error_type FROM group_publish_logs WHERE chat_id=?",
        (-1001,),
    ).fetchone()
    conn.close()

    assert row["status"] == "publish_error"
    assert row["publish_count"] == 0
    assert row["last_publish_at"] is None
    assert log["actor_type"] == "admin"
    assert log["status"] == "failed"
    assert log["error_type"] == "BotPermissionError"


def test_manual_disable_survives_group_refresh(tmp_path):
    get_db = make_db(tmp_path)
    ensure_schema(get_db)
    _upsert_group(get_db, -1001, "Test", Owner(11))

    conn = get_db()
    conn.execute("UPDATE bot_groups SET enabled=0,status='disabled' WHERE chat_id=?", (-1001,))
    conn.commit()
    conn.close()

    _upsert_group(get_db, -1001, "Renamed", Owner(11))

    conn = get_db()
    row = conn.execute(
        "SELECT enabled,status FROM bot_groups WHERE chat_id=?",
        (-1001,),
    ).fetchone()
    conn.close()

    assert row["enabled"] == 0
    assert row["status"] == "disabled"
