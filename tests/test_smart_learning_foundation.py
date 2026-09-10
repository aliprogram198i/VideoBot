from __future__ import annotations

import json
import sqlite3

import pytest

from downloader.smart_learning_foundation import (
    create_dataset_version,
    dataset_rows,
    ensure_foundation_schema,
    record_attempt_trace,
)


def _db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE smart_telemetry (id INTEGER PRIMARY KEY)")
    conn.execute(
        """CREATE TABLE smart_dataset (
            telemetry_id INTEGER NOT NULL,
            candidate_index INTEGER NOT NULL,
            label INTEGER NOT NULL,
            features_json TEXT NOT NULL
        )"""
    )
    for telemetry_id in range(1, 21):
        conn.execute("INSERT INTO smart_telemetry(id) VALUES(?)", (telemetry_id,))
        for candidate_index in range(3):
            conn.execute(
                "INSERT INTO smart_dataset VALUES(?,?,?,?)",
                (telemetry_id, candidate_index, int(candidate_index == 0), json.dumps({"id": telemetry_id, "candidate": candidate_index})),
            )
    conn.commit()
    conn.close()


def test_dataset_version_is_deterministic_and_grouped(tmp_path):
    path = tmp_path / "smart.db"
    _db(path)

    first = create_dataset_version(path, version="dataset-v1", seed="fixed-seed")
    assert first["telemetry_count"] == 20
    assert first["candidate_count"] == 60
    assert first["train_count"] + first["validation_count"] + first["unseen_count"] == 20

    train = dataset_rows(path, version="dataset-v1", split="train")
    unseen = dataset_rows(path, version="dataset-v1", split="unseen")
    assert {row["telemetry_id"] for row in train}.isdisjoint({row["telemetry_id"] for row in unseen})
    assert len(train) % 3 == 0
    assert len(unseen) % 3 == 0


def test_dataset_version_cannot_be_replaced(tmp_path):
    path = tmp_path / "smart.db"
    _db(path)
    create_dataset_version(path, version="dataset-v1", seed="fixed-seed")
    with pytest.raises(ValueError, match="already exists"):
        create_dataset_version(path, version="dataset-v1", seed="other-seed")


def test_attempt_trace_rejects_raw_urls(tmp_path):
    path = tmp_path / "smart.db"
    _db(path)
    conn = sqlite3.connect(path)
    ensure_foundation_schema(conn)
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="raw URL"):
        record_attempt_trace(
            path,
            telemetry_id=1,
            primary_strategy="primary",
            candidate_chain=["primary", "https://secret.example/video"],
            selected_strategy="primary",
            fallback_stage=None,
            final_outcome="success",
        )


def test_attempt_trace_is_updatable(tmp_path):
    path = tmp_path / "smart.db"
    _db(path)
    conn = sqlite3.connect(path)
    ensure_foundation_schema(conn)
    conn.commit()
    conn.close()

    record_attempt_trace(
        path,
        telemetry_id=1,
        primary_strategy="primary",
        candidate_chain=["primary", "validator", "yoinku"],
        selected_strategy="yoinku",
        fallback_stage="secondary",
        final_outcome="success",
    )
    record_attempt_trace(
        path,
        telemetry_id=1,
        primary_strategy="primary",
        candidate_chain=["primary", "yoinku"],
        selected_strategy="yoinku",
        fallback_stage="secondary",
        final_outcome="success",
    )

    conn = sqlite3.connect(path)
    row = conn.execute("SELECT candidate_chain_json FROM smart_attempt_traces WHERE telemetry_id=1").fetchone()
    conn.close()
    assert json.loads(row[0]) == ["primary", "yoinku"]


def test_invalid_split_is_rejected(tmp_path):
    path = tmp_path / "smart.db"
    _db(path)
    create_dataset_version(path, version="dataset-v1", seed="fixed-seed")
    with pytest.raises(ValueError, match="invalid split"):
        dataset_rows(path, version="dataset-v1", split="test")
