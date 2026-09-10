import sqlite3

from plugins.admin_backup_recovery import register_admin_backup_recovery
from plugins.admin_common import authorize
from plugins.admin_security_center import register_admin_security_center


class _App:
    def __init__(self):
        self.handlers = {}
        self.bot_data = {}

    def add_handler(self, handler, group=0):
        self.handlers.setdefault(group, []).append(handler)


class _User:
    def __init__(self, user_id):
        self.id = user_id


class _Update:
    def __init__(self, user_id):
        self.effective_user = _User(user_id)


def _db(tmp_path):
    path = tmp_path / "admin.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE admin_roles (user_id INTEGER PRIMARY KEY, role TEXT, permissions TEXT)"
    )
    conn.execute(
        "INSERT INTO admin_roles VALUES (2, 'viewer', '{\"security.view\": true, \"database.backup\": true}')"
    )
    conn.commit()
    conn.close()

    def get_db():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db

    return get_db


def test_phase3_permissions_are_fail_closed(tmp_path):
    get_db = _db(tmp_path)
    assert authorize(_Update(1), get_db, 1, "security.view") is True
    assert authorize(_Update(2), get_db, 1, "security.view") is True
    assert authorize(_Update(2), get_db, 1, "database.backup") is True
    assert authorize(_Update(2), get_db, 1, "roles.manage") is False
    assert authorize(_Update(3), get_db, 1, "security.view") is False


def test_phase3_handlers_register_once(tmp_path):
    get_db = _db(tmp_path)
    app = _App()
    register_admin_backup_recovery(app, 1, get_db)
    register_admin_security_center(app, 1, get_db)
    patterns = [
        getattr(handler.pattern, "pattern", "")
        for handlers in app.handlers.values()
        for handler in handlers
    ]
    assert patterns.count("^admin_backup_recovery$") == 1
    assert patterns.count("^admin_security$") == 1
    assert app.bot_data["get_db"] is get_db
