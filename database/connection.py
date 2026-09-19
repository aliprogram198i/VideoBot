"""Small, dependency-free SQLite boundary.

This module owns connection policy only. Schema creation/migrations remain
outside it so the legacy database cannot be rewritten accidentally during the
refactor.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from data_layer import DB_FILE as CANONICAL_DB_FILE, get_db as canonical_get_db


class Database:
    """Explicit SQLite connection factory for application services."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        # Canonical application DB path delegates to data_layer. Other explicit
        # paths remain supported for tests/tools without changing their behavior.
        if str(self.path) == str(Path(CANONICAL_DB_FILE)):
            connection = canonical_get_db()
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def connect(path: str | Path) -> sqlite3.Connection:
    """Compatibility helper for one-shot reads/writes."""
    return Database(path).connect()
