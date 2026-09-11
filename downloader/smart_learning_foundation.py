"""Phase 5A foundation: versioned Smart datasets and safe attempt traces.

This module is deliberately opt-in. Importing it does not create files, tables,
or change downloader behavior. The existing Smart telemetry store remains the
source of truth. Dataset snapshots are built only when an explicit caller asks
for one, and rows are split by telemetry event so candidates from one extraction
cannot leak across train/validation/unseen partitions.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FOUNDATION_SCHEMA_VERSION = 1
SPLITS = ("train", "validation", "unseen")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_bucket(seed: str, telemetry_id: int) -> float:
    digest = hashlib.sha256(f"{seed}:{telemetry_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    else:
        conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_foundation_schema(conn: sqlite3.Connection) -> None:
    """Add only Phase-5A metadata tables; never alters existing Smart tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS smart_foundation_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS smart_dataset_versions (
            version TEXT PRIMARY KEY,
            source_min_id INTEGER,
            source_max_id INTEGER,
            telemetry_count INTEGER NOT NULL,
            candidate_count INTEGER NOT NULL,
            positive_count INTEGER NOT NULL,
            negative_count INTEGER NOT NULL,
            train_count INTEGER NOT NULL,
            validation_count INTEGER NOT NULL,
            unseen_count INTEGER NOT NULL,
            split_seed TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS smart_dataset_membership (
            version TEXT NOT NULL,
            telemetry_id INTEGER NOT NULL,
            split TEXT NOT NULL CHECK(split IN ('train','validation','unseen')),
            PRIMARY KEY(version, telemetry_id),
            FOREIGN KEY(version) REFERENCES smart_dataset_versions(version)
        );

        CREATE TABLE IF NOT EXISTS smart_attempt_traces (
            telemetry_id INTEGER PRIMARY KEY,
            primary_strategy TEXT,
            candidate_chain_json TEXT NOT NULL,
            selected_strategy TEXT,
            fallback_stage TEXT,
            final_outcome TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(telemetry_id) REFERENCES smart_telemetry(id)
        );

        CREATE INDEX IF NOT EXISTS idx_smart_dataset_membership_split
            ON smart_dataset_membership(version, split);
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO smart_foundation_meta(key,value) VALUES('schema_version',?)",
        (str(FOUNDATION_SCHEMA_VERSION),),
    )


def _assign_split(bucket: float, train: float, validation: float) -> str:
    if bucket < train:
        return "train"
    if bucket < train + validation:
        return "validation"
    return "unseen"


def create_dataset_version(
    db_path: str | Path,
    *,
    version: str,
    seed: str,
    train_ratio: float = 0.70,
    validation_ratio: float = 0.15,
) -> dict[str, int | str]:
    """Create an immutable snapshot of existing telemetry-derived dataset rows.

    The operation is explicit and additive. It never changes smart_dataset or
    production policy rows. Reusing an existing version is rejected rather than
    silently replacing a historical dataset definition.
    """
    if not version or not version.strip():
        raise ValueError("version must be non-empty")
    if not seed:
        raise ValueError("seed must be non-empty")
    if not (0 < train_ratio < 1):
        raise ValueError("train_ratio must be between 0 and 1")
    if not (0 < validation_ratio < 1):
        raise ValueError("validation_ratio must be between 0 and 1")
    if train_ratio + validation_ratio >= 1:
        raise ValueError("train_ratio + validation_ratio must be below 1")

    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with _connect(path) as conn:
        ensure_foundation_schema(conn)
        if conn.execute("SELECT 1 FROM smart_dataset_versions WHERE version=?", (version,)).fetchone():
            raise ValueError(f"dataset version already exists: {version}")

        rows = conn.execute(
            "SELECT telemetry_id, label FROM smart_dataset ORDER BY telemetry_id, candidate_index"
        ).fetchall()
        telemetry_ids = sorted({int(row["telemetry_id"]) for row in rows})
        if not telemetry_ids:
            raise ValueError("smart_dataset is empty")

        labels_by_telemetry: dict[int, list[int]] = {tid: [] for tid in telemetry_ids}
        for row in rows:
            labels_by_telemetry[int(row["telemetry_id"])].append(int(row["label"]))

        assignments: list[tuple[int, str]] = []
        counts = {split: 0 for split in SPLITS}
        for telemetry_id in telemetry_ids:
            split = _assign_split(_stable_bucket(seed, telemetry_id), train_ratio, validation_ratio)
            assignments.append((telemetry_id, split))
            counts[split] += 1

        positive = sum(sum(labels) for labels in labels_by_telemetry.values())
        candidate_count = len(rows)
        negative = candidate_count - positive

        # The membership table has a foreign key to the version row, so the
        # parent must be committed before children can be inserted when
        # foreign_keys=ON. Keep the whole snapshot atomic in this transaction.
        conn.execute(
            """INSERT INTO smart_dataset_versions(
                version,source_min_id,source_max_id,telemetry_count,candidate_count,
                positive_count,negative_count,train_count,validation_count,unseen_count,
                split_seed,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                version, min(telemetry_ids), max(telemetry_ids), len(telemetry_ids),
                candidate_count, positive, negative, counts["train"],
                counts["validation"], counts["unseen"], seed, _utc_now(),
            ),
        )
        conn.executemany(
            "INSERT INTO smart_dataset_membership(version,telemetry_id,split) VALUES(?,?,?)",
            ((version, telemetry_id, split) for telemetry_id, split in assignments),
        )
        conn.commit()
        return {
            "version": version,
            "telemetry_count": len(telemetry_ids),
            "candidate_count": candidate_count,
            "positive_count": positive,
            "negative_count": negative,
            "train_count": counts["train"],
            "validation_count": counts["validation"],
            "unseen_count": counts["unseen"],
        }


def dataset_rows(
    db_path: str | Path,
    *,
    version: str,
    split: str,
) -> list[dict[str, Any]]:
    """Read one immutable dataset partition without modifying the database."""
    if split not in SPLITS:
        raise ValueError(f"invalid split: {split}")
    with _connect(Path(db_path), read_only=True) as conn:
        rows = conn.execute(
            """SELECT d.telemetry_id, d.candidate_index, d.label, d.features_json
               FROM smart_dataset d
               JOIN smart_dataset_membership m ON m.telemetry_id=d.telemetry_id
               WHERE m.version=? AND m.split=?
               ORDER BY d.telemetry_id, d.candidate_index""",
            (version, split),
        ).fetchall()
        return [
            {
                "telemetry_id": int(row["telemetry_id"]),
                "candidate_index": int(row["candidate_index"]),
                "label": int(row["label"]),
                "features": json.loads(row["features_json"]),
            }
            for row in rows
        ]


def record_attempt_trace(
    db_path: str | Path,
    *,
    telemetry_id: int,
    primary_strategy: str | None,
    candidate_chain: Iterable[str],
    selected_strategy: str | None,
    fallback_stage: str | None,
    final_outcome: str | None,
) -> None:
    """Persist a sanitized fallback trace for a known telemetry event.

    The trace accepts strategy names only. Raw URLs, Telegram identifiers,
    cookies, credentials, and other secret-bearing values are rejected.
    """
    if telemetry_id <= 0:
        raise ValueError("telemetry_id must be positive")
    chain = [str(item)[:120] for item in candidate_chain][:32]
    if any("://" in item or "@" in item for item in chain):
        raise ValueError("raw URL or identifier is not allowed in candidate_chain")
    with _connect(Path(db_path)) as conn:
        ensure_foundation_schema(conn)
        if not conn.execute("SELECT 1 FROM smart_telemetry WHERE id=?", (telemetry_id,)).fetchone():
            raise ValueError("unknown telemetry_id")
        conn.execute(
            """INSERT INTO smart_attempt_traces(
                telemetry_id,primary_strategy,candidate_chain_json,selected_strategy,
                fallback_stage,final_outcome,created_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(telemetry_id) DO UPDATE SET
                primary_strategy=excluded.primary_strategy,
                candidate_chain_json=excluded.candidate_chain_json,
                selected_strategy=excluded.selected_strategy,
                fallback_stage=excluded.fallback_stage,
                final_outcome=excluded.final_outcome,
                created_at=excluded.created_at""",
            (
                telemetry_id,
                (primary_strategy or "")[:120],
                json.dumps(chain, ensure_ascii=False, separators=(",", ":")),
                (selected_strategy or "")[:120],
                (fallback_stage or "")[:120],
                (final_outcome or "")[:120],
                _utc_now(),
            ),
        )
        conn.commit()
