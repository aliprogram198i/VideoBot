"""Canonical active-user statistics for the admin surfaces.

This bootstrap plugin keeps the existing database schema and all stored data
unchanged. It centralizes the definition of an active user so the dashboard
and Gemini user analysis use the same period-based metric.
"""

from datetime import datetime, timedelta
from typing import Any


def register_admin_stats_consistency(bot_module: Any) -> None:
    """Install the canonical active-user metric without changing DB schema."""

    def canonical_active_users(days: int = 30) -> int:
        days = max(1, int(days))
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = bot_module.get_db()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM users "
                "WHERE last_seen IS NOT NULL AND last_seen >= ?",
                (cutoff,),
            ).fetchone()
            return int(row[0] or 0)
        finally:
            conn.close()

    bot_module.get_canonical_active_users_count = canonical_active_users

    original_ai_users_data = bot_module.get_ai_users_data

    def get_ai_users_data_consistent():
        data = original_ai_users_data()
        data["active"] = canonical_active_users(30)
        return data

    bot_module.get_ai_users_data = get_ai_users_data_consistent

    original_dashboard_data = bot_module.get_admin_dashboard_data

    def get_admin_dashboard_data_consistent(days=30):
        data = original_dashboard_data(days)
        data["active_users"] = canonical_active_users(days)
        return data

    bot_module.get_admin_dashboard_data = get_admin_dashboard_data_consistent
