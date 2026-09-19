from types import SimpleNamespace

from plugins.admin_discussion_publisher import _capture_automatic_forward


class FakeDB:
    def __init__(self):
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE discussion_links (
                channel_id INTEGER PRIMARY KEY,
                channel_title TEXT NOT NULL DEFAULT '',
                channel_username TEXT,
                discussion_chat_id INTEGER NOT NULL UNIQUE,
                discussion_title TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE discussion_threads (
                discussion_chat_id INTEGER NOT NULL,
                discussion_message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                channel_post_id INTEGER NOT NULL,
                captured_at TEXT NOT NULL,
                PRIMARY KEY (discussion_chat_id, discussion_message_id)
            );
            """
        )

    def __call__(self):
        return self.conn


def test_capture_automatic_channel_forward():
    db = FakeDB()
    message = SimpleNamespace(
        is_automatic_forward=True,
        message_id=9001,
        chat=SimpleNamespace(id=-100222, title="Test Discussion"),
        forward_origin=SimpleNamespace(
            type="channel",
            chat=SimpleNamespace(id=-100111, title="Test Channel", username="testchannel"),
            message_id=42,
        ),
    )

    assert _capture_automatic_forward(db, message) is True

    link = db.conn.execute("SELECT * FROM discussion_links").fetchone()
    thread = db.conn.execute("SELECT * FROM discussion_threads").fetchone()
    assert link["channel_id"] == -100111
    assert link["discussion_chat_id"] == -100222
    assert thread["channel_post_id"] == 42
    assert thread["discussion_message_id"] == 9001


def test_non_automatic_message_is_ignored():
    db = FakeDB()
    message = SimpleNamespace(
        is_automatic_forward=False,
        message_id=1,
        chat=SimpleNamespace(id=-100222),
    )
    assert _capture_automatic_forward(db, message) is False
    assert db.conn.execute("SELECT COUNT(*) FROM discussion_links").fetchone()[0] == 0
