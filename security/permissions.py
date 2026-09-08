"""Fail-closed identity checks used outside Telegram handler code."""

from __future__ import annotations


def is_admin_id(user_id: int | None, owner_id: int) -> bool:
    """Return true only for the configured owner identity."""
    if user_id is None:
        return False
    if owner_id <= 0:
        raise ValueError("owner_id must be positive")
    return int(user_id) == int(owner_id)
