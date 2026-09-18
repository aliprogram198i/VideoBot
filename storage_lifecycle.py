"""Bounded lifecycle cleanup for AliBot transient storage.

Only /app/tmp (or the explicitly configured TMPDIR when it is inside /app/tmp)
is eligible. Persistent /app/data is never touched.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


DEFAULT_TMP_ROOT = Path("/app/tmp")
DEFAULT_MAX_AGE_SECONDS = 12 * 60 * 60
DEFAULT_MAX_ENTRIES = 200


def cleanup_transient_storage(
    *,
    root: str | os.PathLike[str] = DEFAULT_TMP_ROOT,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> dict[str, int]:
    target = Path(root).resolve()
    allowed = DEFAULT_TMP_ROOT.resolve()
    if target != allowed:
        return {"removed_files": 0, "removed_dirs": 0, "skipped": 1}

    if not target.is_dir():
        return {"removed_files": 0, "removed_dirs": 0, "skipped": 0}

    now = time.time()
    removed_files = 0
    removed_dirs = 0
    scanned = 0

    try:
        entries = sorted(target.iterdir(), key=lambda p: p.stat().st_mtime)
    except OSError:
        return {"removed_files": 0, "removed_dirs": 0, "skipped": 1}

    for entry in entries:
        if scanned >= max_entries:
            break
        scanned += 1
        try:
            if entry.is_symlink():
                continue
            age = now - entry.stat().st_mtime
            if age < max_age_seconds:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
                removed_dirs += 1
            elif entry.is_file():
                entry.unlink()
                removed_files += 1
        except OSError:
            continue

    return {
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "skipped": 0,
    }


def cleanup_on_startup() -> dict[str, int]:
    return cleanup_transient_storage()
