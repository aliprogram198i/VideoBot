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
