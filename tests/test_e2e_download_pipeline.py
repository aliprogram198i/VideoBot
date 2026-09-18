from __future__ import annotations

import sqlite3

import data_layer


def test_delivery_to_ledger_pipeline(tmp_path, monkeypatch):
    db_path = tmp_path / "pipeline.db"
    monkeypatch.setattr(data_layer, "DB_FILE", str(db_path))
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, downloads INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, url TEXT, website TEXT, media_type TEXT, quality TEXT, created_at TEXT)")
    conn.execute("INSERT INTO users(user_id, downloads) VALUES(1, 0)")
    conn.commit()
    conn.close()

    # The delivery step is represented by the caller only invoking the ledger
    # after its send operation completed successfully.
    delivered = True
    if delivered:
        data_layer.record_download(
            user_id=1,
            username="ali",
            url="https://example.com/video",
            website="example",
            media_type="video",
            quality="720p",
            created_at="2026-09-18T00:00:00+00:00",
        )

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == 1
    assert conn.execute("SELECT downloads FROM users WHERE user_id=1").fetchone()[0] == 1
    conn.close()
