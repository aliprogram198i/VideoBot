import sqlite3
from pathlib import Path

import data_layer


def _schema(conn):
    conn.executescript("""
    CREATE TABLE users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        downloads INTEGER DEFAULT 0
    );
    CREATE TABLE downloads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        url TEXT,
        website TEXT,
        media_type TEXT,
        quality TEXT,
        created_at TEXT
    );
    """)


def test_record_download_is_atomic_and_updates_ledger(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(data_layer, "DB_FILE", str(db))
    conn = sqlite3.connect(db)
    _schema(conn)
    conn.execute("INSERT INTO users(user_id, username, downloads) VALUES (1, 'ali', 0)")
    conn.commit()
    conn.close()

    data_layer.record_download(
        user_id=1,
        username="ali",
        url="https://example.com/video",
        website="Example",
        media_type="video",
        quality="720p",
        created_at="2026-01-01T00:00:00",
    )

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT downloads FROM users WHERE user_id = 1").fetchone()
    item = conn.execute("SELECT url, media_type, quality FROM downloads WHERE user_id = 1").fetchone()
    conn.close()

    assert row == (1,)
    assert item == ("https://example.com/video", "video", "720p")


def test_download_counts_uses_single_canonical_ledger(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(data_layer, "DB_FILE", str(db))
    conn = sqlite3.connect(db)
    _schema(conn)
    conn.executemany(
        "INSERT INTO downloads(user_id, website, media_type, created_at) VALUES (?, ?, ?, ?)",
        [
            (1, "YouTube", "video", "2026-01-01T00:00:00"),
            (1, "Instagram", "video", "2026-01-01T00:00:01"),
            (1, "YouTube", "audio", "2026-01-01T00:00:02"),
        ],
    )
    conn.commit()
    conn.close()

    assert data_layer.download_counts() == {"downloads": 3, "videos": 2, "audio": 1}


def test_multi_url_contract_deduplicates_and_bounds():
    from plugins.user_features import _extract_urls

    urls = _extract_urls(
        "https://example.com/a https://example.com/a "
        "https://example.com/b, https://example.com/c"
    )
    assert urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
