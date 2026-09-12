import sqlite3

from plugins import admin_observability_center as observability


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE smart_telemetry (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            source_host TEXT,
            best_kind TEXT,
            extraction_ms REAL NOT NULL
        );
        CREATE TABLE smart_outcomes (
            id INTEGER PRIMARY KEY,
            telemetry_id INTEGER NOT NULL,
            success INTEGER NOT NULL,
            selected_kind TEXT,
            failure_reason TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO smart_telemetry VALUES
            (1, '2099-01-01T00:00:00+00:00', 'example.com', 'hls', 120.0),
            (2, '2099-01-01T00:01:00+00:00', 'example.com', 'progressive', 240.0),
            (3, '2099-01-01T00:02:00+00:00', 'other.example', 'dash', 360.0);
        INSERT INTO smart_outcomes VALUES
            (1, 1, 1, 'hls', NULL, '2099-01-01T00:00:01+00:00'),
            (2, 2, 0, 'progressive', 'timeout', '2099-01-01T00:01:01+00:00'),
            (3, 3, 1, 'dash', NULL, '2099-01-01T00:02:01+00:00');
        """
    )
    conn.commit()
    conn.close()


def test_collect_observability_is_read_only(tmp_path, monkeypatch):
    db = tmp_path / "smart_learning.db"
    _make_db(db)
    before = db.read_bytes()
    monkeypatch.setenv("SMART_DATA_DIR", str(tmp_path))

    data = observability.collect_observability()

    assert data["available"] is True
    assert data["telemetry"] == 3
    assert data["outcomes"] == 3
    assert data["successes"] == 2
    assert data["failures"] == 1
    assert data["success_rate"] == 66.7
    assert any(row["host"] == "example.com" for row in data["platforms"])
    assert any(row["reason"] == "timeout" for row in data["failures_by_reason"])
    assert db.read_bytes() == before


def test_missing_telemetry_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_DATA_DIR", str(tmp_path))
    data = observability.collect_observability()
    assert data["available"] is False
    assert data["telemetry"] == 0
