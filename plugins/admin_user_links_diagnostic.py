"""Runtime diagnostics for admin user download-link visibility."""
from __future__ import annotations
import logging
import re
from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes
logger = logging.getLogger(__name__)
_PATTERN = re.compile(r"^(?:admin_user_view|user)_(\d+)$")
async def _diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE, get_db) -> None:
    query = update.callback_query
    match = _PATTERN.match(query.data or "")
    if not match:
        return
    user_id = int(match.group(1))
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS count FROM downloads WHERE user_id = ? AND COALESCE(url, '') <> ''", (user_id,)).fetchone()
        total = conn.execute("SELECT COUNT(*) AS count FROM downloads").fetchone()["count"]
        logger.info("ADMIN_LINK_DIAG user_id=%s user_rows=%s total_download_rows=%s", user_id, int(row["count"] if row else 0), int(total or 0))
    except Exception:
        logger.exception("ADMIN_LINK_DIAG failed for user_id=%s", user_id)
    finally:
        conn.close()
def register_admin_user_links_diagnostic(app, get_db) -> None:
    app.add_handler(CallbackQueryHandler(lambda u, c: _diagnose(u, c, get_db), pattern=r"^(?:admin_user_view|user)_\d+$"), group=-201)
