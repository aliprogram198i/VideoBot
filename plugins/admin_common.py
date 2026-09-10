"""Shared, fail-closed administration primitives.

The admin layer is intentionally isolated from downloader/user-facing logic.
This module centralizes permission names and authorization so future admin
modules do not each invent their own access-control rules.
"""

from __future__ import annotations

import json
from typing import Any


# Stable permission identifiers. The owner has the wildcard permission.
PERMISSIONS = {
    "center.view",
    "users.view",
    "history.view",
    "history.clear",
    "analytics.view",
    "operations.view",
    "system.health",
    "audit.view",
    "roles.view",
    "roles.manage",
    "broadcast.send",
    "security.view",
    "database.backup",
}


def authorize(update: Any, get_db, owner_id: int, permission: str) -> bool:
    """Return True only when the current Telegram user has the permission.

    Authorization is fail-closed. The configured owner is always allowed;
    other users must have an explicit role with the requested permission.
    Malformed/missing role data never grants access.
    """
    user = getattr(update, "effective_user", None)
    if not user:
        return False
    user_id = int(user.id)

    if user_id == int(owner_id):
        return True

    if permission not in PERMISSIONS:
        return False

    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT role, permissions FROM admin_roles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return False

    if not row:
        return False

    try:
        permissions = json.loads(row["permissions"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return False

    return bool(permissions.get("*") or permissions.get(permission))
