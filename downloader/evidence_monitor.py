"""Bounded read-only evidence telemetry for isolated staging runs."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path


async def run_periodic(db_path: str | Path, *, interval_seconds: int = 60) -> None:
    """Log aggregate paired-evidence counts without exposing request data."""
    path = Path(db_path)
    while True:
        try:
            if path.exists():
                with sqlite3.connect(path, timeout=5) as conn:
                    total_rows = int(conn.execute("SELECT COUNT(*) FROM resolver_evidence").fetchone()[0])
                    distinct_samples = int(conn.execute("SELECT COUNT(DISTINCT sample_id) FROM resolver_evidence").fetchone()[0])
                    paired_samples = int(conn.execute("""
                        SELECT COUNT(*) FROM (
                            SELECT sample_id
                            FROM resolver_evidence
                            WHERE resolver IN ('legacy_extractor','smart_media','browser_media','cobalt')
                            GROUP BY sample_id
                            HAVING COUNT(DISTINCT resolver) >= 2
                        )
                    """).fetchone()[0])
                    complete_samples = int(conn.execute("""
                        SELECT COUNT(*) FROM (
                            SELECT sample_id
                            FROM resolver_evidence
                            WHERE resolver IN ('legacy_extractor','smart_media','browser_media','cobalt')
                            GROUP BY sample_id
                            HAVING COUNT(DISTINCT resolver) = 4
                        )
                    """).fetchone()[0])
                    print(
                        f"📈 Paired Evidence Monitor: rows={total_rows} samples={distinct_samples} "
                        f"paired>=2={paired_samples} complete4={complete_samples}",
                        flush=True,
                    )
        except Exception:
            # Monitoring must never affect the downloader runtime.
            pass
        await asyncio.sleep(max(30, int(interval_seconds)))


__all__ = ["run_periodic"]
