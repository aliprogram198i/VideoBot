"""Persistent telemetry, dataset, evaluation, and policy learning for Smart.

This module is intentionally independent from Telegram/download logic. It stores
only technical extraction features (not Telegram user identifiers or raw source
URLs) and keeps every learned policy version immutable.

Production persistence is selected from SMART_DATA_DIR when set, otherwise from
/app/data/smart when that directory exists. A local .smart_data fallback is used
for development only; production should mount /app/data as a Railway Volume.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


SCHEMA_VERSION = 1
DEFAULT_POLICY_VERSION = "smart-policy-v1"
BASE_WEIGHTS = {
    "kind:hls": 30.0,
    "kind:dash": 25.0,
    "kind:progressive": 20.0,
    "kind:iframe": 0.0,
    "discovered:video": 12.0,
    "discovered:source": 10.0,
    "discovered:script": 6.0,
    "discovered:attribute": 3.0,
    "discovered:iframe": 0.0,
    "status:200": 5.0,
    "type:m3u8": 8.0,
    "type:dash+xml": 8.0,
    "type:video": 8.0,
    "depth:-1": 0.0,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_host(url: str) -> str | None:
    try:
        host = urlparse(url).hostname
    except Exception:
        return None
    return host.lower() if host else None


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", "ignore")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _data_root() -> Path:
    configured = os.getenv("SMART_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    railway_data = Path("/app/data")
    if railway_data.is_dir():
        return railway_data / "smart"
    return Path(".smart_data")


class SmartTelemetryStore:
    """Durable SQLite store for Smart telemetry and learned policies."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root) if root is not None else _data_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "smart_learning.db"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS smart_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS smart_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source_host TEXT,
                    candidate_count INTEGER NOT NULL,
                    valid_candidate_count INTEGER NOT NULL,
                    invalid_candidate_count INTEGER NOT NULL,
                    best_kind TEXT,
                    best_discovered_by TEXT,
                    best_depth INTEGER,
                    best_status INTEGER,
                    best_content_type TEXT,
                    best_rank_score REAL,
                    extraction_ms REAL NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    policy_version TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS smart_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telemetry_id INTEGER NOT NULL UNIQUE,
                    success INTEGER NOT NULL CHECK(success IN (0,1)),
                    selected_url_key TEXT,
                    selected_kind TEXT,
                    failure_reason TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(telemetry_id) REFERENCES smart_telemetry(id)
                );

                CREATE TABLE IF NOT EXISTS smart_dataset (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telemetry_id INTEGER NOT NULL,
                    candidate_index INTEGER NOT NULL,
                    label INTEGER NOT NULL CHECK(label IN (0,1)),
                    features_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(telemetry_id, candidate_index),
                    FOREIGN KEY(telemetry_id) REFERENCES smart_telemetry(id)
                );

                CREATE TABLE IF NOT EXISTS smart_policy_versions (
                    version TEXT PRIMARY KEY,
                    parent_version TEXT,
                    weights_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS smart_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL,
                    dataset_size INTEGER NOT NULL,
                    accuracy REAL NOT NULL,
                    positive_precision REAL NOT NULL,
                    positive_recall REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(version) REFERENCES smart_policy_versions(version)
                );

                CREATE INDEX IF NOT EXISTS idx_smart_telemetry_host
                    ON smart_telemetry(source_host);
                CREATE INDEX IF NOT EXISTS idx_smart_outcomes_success
                    ON smart_outcomes(success);
                CREATE INDEX IF NOT EXISTS idx_smart_dataset_telemetry
                    ON smart_dataset(telemetry_id);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO smart_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO smart_policy_versions
                    (version,parent_version,weights_json,metrics_json,status,created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    DEFAULT_POLICY_VERSION,
                    None,
                    _json(BASE_WEIGHTS),
                    _json({"source": "deterministic-baseline", "dataset_size": 0}),
                    "production",
                    _utc_now(),
                ),
            )
            conn.commit()

    @staticmethod
    def _candidate_features(candidate: Any, result: Any) -> dict[str, Any]:
        validation = result
        content_type = (getattr(validation, "content_type", None) or "").lower()
        kind = str(candidate.kind)
        discovered = str(candidate.discovered_by)
        status = getattr(validation, "status", None)
        return {
            "kind": kind,
            "discovered_by": discovered,
            "depth": int(candidate.depth),
            "valid": bool(getattr(validation, "valid", False)),
            "status": int(status) if isinstance(status, int) else None,
            "content_type": content_type[:120],
            "score": float(candidate.score),
            "url_key": _url_key(candidate.url),
            "host": _safe_host(candidate.url),
        }

    def record_extraction(
        self,
        result: Any,
        *,
        elapsed_ms: float,
        policy_version: str = DEFAULT_POLICY_VERSION,
    ) -> int:
        """Persist one extraction without storing raw URLs."""
        best = getattr(result, "best_media", None)
        candidates = []
        for ranked in getattr(result, "ranked_candidates", ()):
            candidates.append(self._candidate_features(ranked.candidate, ranked))

        source_host = _safe_host(getattr(result, "source_url", ""))
        best_candidate = best.candidate if best else None
        row = (
            _utc_now(), source_host,
            int(getattr(result, "candidate_count", 0)),
            int(getattr(result, "valid_candidate_count", 0)),
            int(getattr(result, "invalid_candidate_count", 0)),
            getattr(best_candidate, "kind", None),
            getattr(best_candidate, "discovered_by", None),
            getattr(best_candidate, "depth", None),
            getattr(best, "status", None) if best else None,
            (getattr(best, "content_type", None) or "")[:120] if best else None,
            float(self._rank_score(best)) if best else None,
            max(0.0, float(elapsed_ms)),
            _json(list(getattr(result, "diagnostics", ()))),
            _json(candidates),
            policy_version,
        )
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO smart_telemetry(
                    created_at,source_host,candidate_count,valid_candidate_count,
                    invalid_candidate_count,best_kind,best_discovered_by,best_depth,
                    best_status,best_content_type,best_rank_score,extraction_ms,
                    diagnostics_json,candidates_json,policy_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                row,
            )
            conn.commit()
            return int(cur.lastrowid)

    @staticmethod
    def _rank_score(result: Any) -> float:
        candidate = getattr(result, "candidate", None)
        if candidate is None:
            return 0.0
        return float(candidate.score)

    def record_outcome(
        self,
        telemetry_id: int,
        *,
        success: bool,
        selected_url: str | None = None,
        selected_kind: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        """Attach the real downstream outcome to one extraction event."""
        if telemetry_id <= 0:
            raise ValueError("telemetry_id must be positive")
        selected_key = _url_key(selected_url) if selected_url else None
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO smart_outcomes(
                    telemetry_id,success,selected_url_key,selected_kind,
                    failure_reason,created_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(telemetry_id) DO UPDATE SET
                    success=excluded.success,
                    selected_url_key=excluded.selected_url_key,
                    selected_kind=excluded.selected_kind,
                    failure_reason=excluded.failure_reason,
                    created_at=excluded.created_at
                """,
                (telemetry_id, int(bool(success)), selected_key,
                 selected_kind, (failure_reason or "")[:500], _utc_now()),
            )
            self._materialize_dataset(conn, telemetry_id)
            conn.commit()

    def _materialize_dataset(self, conn: sqlite3.Connection, telemetry_id: int) -> None:
        telemetry = conn.execute(
            "SELECT candidates_json FROM smart_telemetry WHERE id=?", (telemetry_id,)
        ).fetchone()
        outcome = conn.execute(
            "SELECT success,selected_url_key FROM smart_outcomes WHERE telemetry_id=?",
            (telemetry_id,),
        ).fetchone()
        if not telemetry or not outcome:
            return
        candidates = json.loads(telemetry["candidates_json"] or "[]")
        selected_key = outcome["selected_url_key"]
        success = bool(outcome["success"])
        for index, candidate in enumerate(candidates):
            label = int(success and selected_key and candidate.get("url_key") == selected_key)
            conn.execute(
                """
                INSERT INTO smart_dataset(telemetry_id,candidate_index,label,features_json,created_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(telemetry_id,candidate_index) DO UPDATE SET
                    label=excluded.label,features_json=excluded.features_json
                """,
                (telemetry_id, index, label, _json(candidate), _utc_now()),
            )

    def dataset(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT label,features_json FROM smart_dataset ORDER BY id"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {"label": int(row["label"]), "features": json.loads(row["features_json"])}
            for row in rows
        ]

    def production_policy(self) -> tuple[str, dict[str, float]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT version,weights_json FROM smart_policy_versions WHERE status='production' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return DEFAULT_POLICY_VERSION, dict(BASE_WEIGHTS)
        return row["version"], {k: float(v) for k, v in json.loads(row["weights_json"]).items()}

    def save_policy(
        self,
        *,
        version: str,
        parent_version: str,
        weights: dict[str, float],
        metrics: dict[str, Any],
        status: str = "candidate",
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO smart_policy_versions(version,parent_version,weights_json,metrics_json,status,created_at) VALUES(?,?,?,?,?,?)",
                (version, parent_version, _json(weights), _json(metrics), status, _utc_now()),
            )
            conn.commit()

    def promote_policy(self, version: str) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT version FROM smart_policy_versions WHERE version=?", (version,)).fetchone()
            if not row:
                raise ValueError("unknown policy version")
            conn.execute("UPDATE smart_policy_versions SET status='retired' WHERE status='production'")
            conn.execute("UPDATE smart_policy_versions SET status='production' WHERE version=?", (version,))
            conn.commit()

    def evaluate_weights(self, weights: dict[str, float], rows: Iterable[dict[str, Any]]) -> dict[str, float]:
        data = list(rows)
        if not data:
            return {"dataset_size": 0, "accuracy": 0.0, "positive_precision": 0.0, "positive_recall": 0.0}
        correct = tp = predicted_positive = actual_positive = 0
        for row in data:
            features = row["features"]
            score = self._score_features(features, weights)
            prediction = score >= 0.0
            label = bool(row["label"])
            correct += int(prediction == label)
            tp += int(prediction and label)
            predicted_positive += int(prediction)
            actual_positive += int(label)
        return {
            "dataset_size": len(data),
            "accuracy": correct / len(data),
            "positive_precision": tp / predicted_positive if predicted_positive else 0.0,
            "positive_recall": tp / actual_positive if actual_positive else 0.0,
        }

    @staticmethod
    def _score_features(features: dict[str, Any], weights: dict[str, float]) -> float:
        score = float(features.get("score", 0.0))
        kind = features.get("kind")
        discovered = features.get("discovered_by")
        status = features.get("status")
        content_type = (features.get("content_type") or "").lower()
        score += weights.get(f"kind:{kind}", 0.0)
        score += weights.get(f"discovered:{discovered}", 0.0)
        if status == 200:
            score += weights.get("status:200", 0.0)
        if "mpegurl" in content_type:
            score += weights.get("type:m3u8", 0.0)
        if "dash+xml" in content_type:
            score += weights.get("type:dash+xml", 0.0)
        if content_type.startswith("video/"):
            score += weights.get("type:video", 0.0)
        score -= int(features.get("depth", 0)) * weights.get("depth:-1", 0.0)
        return score

    def learn_policy(self, *, min_rows: int = 100) -> dict[str, Any]:
        """Create a conservative candidate policy from observed labels.

        We learn bounded feature adjustments from empirical positive/negative
        rates. Nothing is promoted automatically; production remains unchanged
        until the candidate passes evaluation and is explicitly promoted.
        """
        rows = self.dataset()
        if len(rows) < min_rows:
            return {"status": "insufficient_data", "dataset_size": len(rows), "required": min_rows}

        parent_version, base = self.production_policy()
        groups: dict[str, list[int]] = {}
        for row in rows:
            f = row["features"]
            keys = [f"kind:{f.get('kind')}", f"discovered:{f.get('discovered_by')}" ]
            if f.get("status") == 200:
                keys.append("status:200")
            for key in keys:
                groups.setdefault(key, []).append(int(row["label"]))

        weights = dict(base)
        for key, labels in groups.items():
            n = len(labels)
            if n < 10:
                continue
            rate = sum(labels) / n
            adjustment = max(-12.0, min(12.0, (rate - 0.5) * 24.0))
            weights[key] = round(base.get(key, 0.0) + adjustment, 4)

        metrics = self.evaluate_weights(weights, rows)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        version = f"smart-policy-{stamp}"
        self.save_policy(version=version, parent_version=parent_version, weights=weights, metrics=metrics)
        return {"status": "candidate_created", "version": version, "parent_version": parent_version, **metrics}


_default_store: SmartTelemetryStore | None = None
_default_lock = threading.Lock()


def get_telemetry_store() -> SmartTelemetryStore:
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = SmartTelemetryStore()
    return _default_store
