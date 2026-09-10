import os
import sqlite3

from plugins.admin_fallback_intelligence import collect_fallback_intelligence


def test_fallback_intelligence_missing_store_is_empty_and_non_mutating(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_DATA_DIR", str(tmp_path / "smart"))
    data = collect_fallback_intelligence()
    assert data["telemetry_total"] == 0
    assert data["outcomes_total"] == 0
    assert not (tmp_path / "smart" / "smart_learning.db").exists()


def test_fallback_intelligence_reads_existing_store_read_only(tmp_path, monkeypatch):
    root = tmp_path / "smart"
    root.mkdir()
    db_path = root / "smart_learning.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE smart_telemetry (
            id INTEGER PRIMARY KEY, created_at TEXT, source_host TEXT,
            candidate_count INTEGER, valid_candidate_count INTEGER,
            invalid_candidate_count INTEGER, best_kind TEXT,
            best_discovered_by TEXT, best_depth INTEGER, best_status INTEGER,
            best_content_type TEXT, best_rank_score REAL, extraction_ms REAL,
            diagnostics_json TEXT, candidates_json TEXT, policy_version TEXT
        );
        CREATE TABLE smart_outcomes (
            id INTEGER PRIMARY KEY, telemetry_id INTEGER UNIQUE, success INTEGER,
            selected_url_key TEXT, selected_kind TEXT, failure_reason TEXT, created_at TEXT
        );
        CREATE TABLE smart_policy_versions (
            version TEXT PRIMARY KEY, parent_version TEXT, weights_json TEXT,
            metrics_json TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE smart_evaluations (
            id INTEGER PRIMARY KEY, version TEXT, dataset_size INTEGER,
            accuracy REAL, positive_precision REAL, positive_recall REAL, created_at TEXT
        );
        INSERT INTO smart_telemetry VALUES
            (1,'2026-09-11T00:00:00Z','example.com',2,1,1,'hls','video',0,200,'application/vnd.apple.mpegurl',50,120.0,'["candidate_rejected:bad"]','[]','smart-policy-v1');
        INSERT INTO smart_outcomes VALUES
            (1,1,1,'abc','hls',NULL,'2026-09-11T00:00:01Z');
        INSERT INTO smart_policy_versions VALUES
            ('smart-policy-v1',NULL,'{}','{}','production','2026-09-11T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    before = os.path.getsize(db_path)
    monkeypatch.setenv("SMART_DATA_DIR", str(root))
    data = collect_fallback_intelligence()
    after = os.path.getsize(db_path)

    assert data["telemetry_total"] == 1
    assert data["outcomes_total"] == 1
    assert data["successes"] == 1
    assert data["success_rate"] == 100.0
    assert data["production_policy"] == "smart-policy-v1"
    assert data["avg_ms"] == 120.0
    assert before == after
