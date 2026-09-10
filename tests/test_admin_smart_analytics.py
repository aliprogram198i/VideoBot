import sqlite3

from plugins.admin_smart_analytics import collect_smart_analytics, _render


def _db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE smart_telemetry (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            source_host TEXT,
            extraction_ms REAL
        );
        CREATE TABLE smart_outcomes (
            id INTEGER PRIMARY KEY,
            telemetry_id INTEGER UNIQUE,
            success INTEGER NOT NULL,
            selected_kind TEXT,
            failure_reason TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE smart_policy_versions (
            version TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE smart_evaluations (
            id INTEGER PRIMARY KEY,
            version TEXT,
            dataset_size INTEGER,
            accuracy REAL,
            positive_precision REAL,
            positive_recall REAL,
            created_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO smart_policy_versions VALUES('smart-policy-v1','production','2026-01-01T00:00:00+00:00')")
    conn.commit()
    return conn


def test_phase4_reads_without_mutating(tmp_path):
    path = tmp_path / "smart_learning.db"
    conn = _db(path)
    before = conn.execute("SELECT COUNT(*) FROM smart_telemetry").fetchone()[0]
    conn.close()

    data = collect_smart_analytics(path)

    assert data["available"] is True
    assert data["current"]["telemetry"] == 0
    assert data["current"]["outcomes"] == 0
    conn = sqlite3.connect(path)
    after = conn.execute("SELECT COUNT(*) FROM smart_telemetry").fetchone()[0]
    assert after == before
    conn.close()


def test_phase4_detects_success_drop(tmp_path, monkeypatch):
    path = tmp_path / "smart_learning.db"
    conn = _db(path)
    for i in range(10):
        conn.execute("INSERT INTO smart_telemetry VALUES(?,?,?,?)", (i + 1, "2026-01-01T00:00:00+00:00", "example.com", 100.0))
        conn.execute("INSERT INTO smart_outcomes VALUES(?,?,?,?,?,?)", (i + 1, i + 1, 1, "hls", None, "2026-01-01T00:00:00+00:00"))
    conn.commit()
    conn.close()

    # Unit-test the anomaly rules with deterministic period stats instead of
    # depending on the wall clock used by SQLite date expressions.
    import plugins.admin_smart_analytics as module
    original = module._period_stats
    calls = iter([
        {"telemetry": 20, "outcomes": 20, "successes": 8, "failures": 12, "success_rate": 40.0, "avg_ms": 100.0, "p95_ms": 120.0},
        {"telemetry": 20, "outcomes": 20, "successes": 18, "failures": 2, "success_rate": 90.0, "avg_ms": 100.0, "p95_ms": 120.0},
    ])
    monkeypatch.setattr(module, "_period_stats", lambda *args: next(calls))
    data = module.collect_smart_analytics(path)
    assert any(item["title"] == "انخفاض معدل النجاح" for item in data["anomalies"])
    assert data["policy"] == "smart-policy-v1"
    assert original is not None


def test_phase4_render_is_bounded_and_safe():
    data = {
        "available": True,
        "current": {"telemetry": 20, "outcomes": 20, "success_rate": 90.0, "avg_ms": 100.0, "p95_ms": 120.0},
        "previous": {"telemetry": 20, "outcomes": 20, "success_rate": 90.0, "avg_ms": 100.0, "p95_ms": 120.0},
        "hosts": [{"host": "example.com", "outcomes": 20, "failure_rate": 10.0, "success_rate": 90.0, "avg_ms": 100.0}],
        "anomalies": [], "recommendations": [], "policy": "smart-policy-v1", "candidates": 0, "evaluations": 0,
    }
    text = _render(data)
    assert len(text) <= 3900
    assert "Smart Analytics" in text
