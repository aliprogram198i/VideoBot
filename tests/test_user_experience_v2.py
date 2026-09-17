import sqlite3

from plugins.user_experience_v2 import _ALLOWED, _get_preferences, _set_preferences, _db


class FakeBot:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def get_db(self):
        return self.conn


def test_preference_choices_are_disjoint_and_complete():
    assert set(_ALLOWED["video"]).isdisjoint(_ALLOWED["audio"])
    assert "video_720" in _ALLOWED["video"]
    assert "audio_320" in _ALLOWED["audio"]


def test_set_preferences_rejects_cross_type_quality_without_db_access():
    class RejectingBot:
        def get_db(self):
            raise AssertionError("database must not be touched for invalid input")

    try:
        _set_preferences(RejectingBot(), 1, "video", "audio_320")
    except ValueError:
        return
    raise AssertionError("cross-type quality must be rejected")


def test_preferences_persist_and_update():
    bot = FakeBot()
    assert _get_preferences(bot, 42) == ("video", "video_720")
    _set_preferences(bot, 42, "audio", "audio_320")
    assert _get_preferences(bot, 42) == ("audio", "audio_320")


def test_library_schema_is_additive_and_user_scoped():
    bot = FakeBot()
    conn = _db(bot)
    conn.execute(
        "INSERT INTO user_favorites(user_id,url,website,media_type,quality,title,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (1, "https://example.com/a", "example.com", "video", "video_720", "A", "2026-09-17T00:00:00"),
    )
    conn.execute(
        "INSERT INTO user_favorites(user_id,url,website,media_type,quality,title,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (2, "https://example.com/a", "example.com", "video", "video_720", "A", "2026-09-17T00:00:00"),
    )
    rows = conn.execute(
        "SELECT user_id,url,quality FROM user_favorites WHERE user_id = ?",
        (1,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["quality"] == "video_720"
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"user_preferences", "user_favorites"}.issubset(tables)
    conn.close()
