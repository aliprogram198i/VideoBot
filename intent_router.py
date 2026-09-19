"""Canonical incoming-update intent classification for AliBot.

The router is deliberately conservative: only explicit, unambiguous states are
claimed. Unknown text remains text and is never silently converted into a
download or admin action.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# State keys used by current user/admin workflows. Keeping this list centralized
# prevents one broad text handler from accidentally consuming another workflow.
_ADMIN_WORKFLOW_KEYS = (
    "rich_broadcast_waiting",
    "library_searching",
    "waiting_broadcast",
    "waiting_user_message",
    "waiting_admin_search",
    "admin_search_mode",
)

class Intent(str, Enum):
    COMMAND = "command"
    URL = "url"
    ADMIN_WORKFLOW = "admin_workflow"
    PENDING_DOWNLOAD = "pending_download"
    TEXT = "text"
    SMART_SEARCH = "smart_search"
    NON_TEXT = "non_text"


def _active_admin_workflow(context: Any, user_id: int | None, admin_id: int | None) -> bool:
    if user_id is None or admin_id is None or user_id != admin_id:
        return False
    user_data = getattr(context, "user_data", {}) or {}
    return any(bool(user_data.get(key)) for key in _ADMIN_WORKFLOW_KEYS)


def classify_text(text: str | None, *, admin_workflow: bool = False, pending_download: bool = False) -> Intent:
    value = (text or "").strip()
    if not value:
        return Intent.NON_TEXT
    if value.startswith("/"):
        return Intent.COMMAND
    if _URL_RE.match(value):
        return Intent.URL
    if admin_workflow:
        return Intent.ADMIN_WORKFLOW
    if pending_download:
        return Intent.PENDING_DOWNLOAD
    return Intent.SMART_SEARCH


def classify_update(update: Any, context: Any, *, admin_id: int | None = None) -> Intent:
    """Return exactly one routing intent without performing side effects."""
    user = getattr(update, "effective_user", None)
    user_id = getattr(user, "id", None)
    message = getattr(update, "message", None)
    text = getattr(message, "text", None)

    if message is None:
        return Intent.NON_TEXT

    if not isinstance(text, str):
        return Intent.NON_TEXT

    user_data = getattr(context, "user_data", {}) or {}
    admin_workflow = _active_admin_workflow(context, user_id, admin_id)
    pending_download = bool(user_data.get("video_url"))

    return classify_text(
        text,
        admin_workflow=admin_workflow,
        pending_download=pending_download,
    )


def is_smart_search_intent(update: Any, context: Any, *, admin_id: int | None = None) -> bool:
    return classify_update(update, context, admin_id=admin_id) is Intent.SMART_SEARCH
