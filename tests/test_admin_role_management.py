import json
import sqlite3

from plugins.admin_common import authorize
from plugins.admin_role_management import ROLE_PERMISSIONS, _remove_role, _upsert_role, register_admin_role_management


class _App:
    def __init__(self):
        self.handlers = {}

    def add_handler(self, handler, group=0):
        self.handlers.setdefault(group, []).append(handler)


class _User:
    def __init__(self, user_id):
        self.id = user_id


class _Update:
    def __init__(self, user_id):
        self.effective_user = _User(user_id)


def _db(tmp_path, with_created_at=True):
    path = tmp_path / "admin.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    created = ", created_at TEXT NOT NULL" if with_created_at else ""
    conn.execute(
        f"CREATE TABLE admin_roles (user_id INTEGER PRIMARY KEY, role TEXT NOT NULL, permissions TEXT NOT NULL DEFAULT '{{}}'{created}, updated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    def get_db():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db

    return get_db


def test_role_presets_never_grant_wildcard(tmp_path):
    get_db = _db(tmp_path)
    for role, permissions in ROLE_PERMISSIONS.items():
        assert "*" not in permissions
        _upsert_role(get_db, 123, role)
        row = get_db().execute("SELECT role, permissions FROM admin_roles WHERE user_id=123").fetchone()
        assert row["role"] == role
        assert json.loads(row["permissions"]) == {permission: True for permission in permissions}


def test_owner_is_always_authorized_but_role_management_is_not_in_presets_except_manager(tmp_path):
    get_db = _db(tmp_path)
    assert authorize(_Update(1), get_db, 1, "roles.manage") is True
    _upsert_role(get_db, 2, "viewer")
    assert authorize(_Update(2), get_db, 1, "roles.manage") is False
    _upsert_role(get_db, 2, "role_manager")
    assert authorize(_Update(2), get_db, 1, "roles.manage") is True


def test_upsert_and_remove_work_with_current_schema(tmp_path):
    get_db = _db(tmp_path, with_created_at=True)
    _upsert_role(get_db, 42, "operator")
    row = get_db().execute("SELECT role FROM admin_roles WHERE user_id=42").fetchone()
    assert row["role"] == "operator"
    assert _remove_role(get_db, 42) is True
    assert _remove_role(get_db, 42) is False


def test_upsert_supports_legacy_role_schema_without_created_at(tmp_path):
    get_db = _db(tmp_path, with_created_at=False)
    _upsert_role(get_db, 42, "backup")
    row = get_db().execute("SELECT role FROM admin_roles WHERE user_id=42").fetchone()
    assert row["role"] == "backup"


def test_role_management_registers_exactly_one_command_handler(tmp_path):
    get_db = _db(tmp_path)
    app = _App()
    register_admin_role_management(app, get_db, 1)
    handlers = [handler for group in app.handlers.values() for handler in group]
    assert len(handlers) == 1
    assert getattr(handlers[0], "commands", None) == {"adminrole"}
