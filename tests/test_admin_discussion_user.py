import sqlite3
from types import SimpleNamespace

from plugins.admin_discussion_user import (
    DiscussionUserManager,
    _invite_hash,
    _normalize_url,
)


class FakeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE mtproto_discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            chat_id INTEGER UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            username TEXT,
            chat_type TEXT NOT NULL DEFAULT 'unknown',
            enabled INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT,
            joined_at TEXT,
            last_verified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)
    def __call__(self):
        return self.conn


def test_normalize_public_link():
    assert _normalize_url("https://t.me/ExampleDiscussion") == "https://t.me/ExampleDiscussion"


def test_normalize_private_link():
    assert _normalize_url("t.me/+AbCd_123") == "https://t.me/+AbCd_123"
    assert _invite_hash("https://t.me/joinchat/AbCd_123") == "AbCd_123"


def test_reject_non_telegram():
    try:
        _normalize_url("https://example.com/group")
    except ValueError:
        pass
    else:
        raise AssertionError("non Telegram URL accepted")


def test_manager_without_config_is_safe():
    manager = DiscussionUserManager(FakeDB())
    assert manager.configured is False


def test_upsert_is_idempotent():
    manager = DiscussionUserManager(FakeDB())
    entity = SimpleNamespace(id=-100123, title="Example", username="example", megagroup=True)
    manager._upsert("https://t.me/Example", entity, "joined", None)
    manager._upsert("https://t.me/Example", entity, "joined", None)
    assert len(manager.list_rows()) == 1
    assert manager.list_rows()[0]["chat_id"] == -100123
