import sqlite3

from telemetry.resolver_outcomes import (
    begin_attempt,
    clear_attempt,
    get_monitor_data,
    record_error,
    record_success,
    record_terminal_failure,
    set_final_resolver,
)


def _db_factory():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # The factory must return the same DB for this test, so use the holder.
    return conn


def test_resolver_monitor_tracks_recovery_and_terminal_failure():
    holder = {"conn": sqlite3.connect(":memory:")}
    holder["conn"].row_factory = sqlite3.Row

    def get_db():
        # Production closes each connection after use; tests need a persistent
        # in-memory handle, so return a proxy that does not close.
        class _Conn:
            def __getattr__(self, name):
                return getattr(holder["conn"], name)

            def close(self):
                pass

        return _Conn()

    begin_attempt("a1", "instagram", "video")
    record_error(get_db, resolver="yt-dlp", error_type="extractor")
    set_final_resolver("cobalt")
    record_success(get_db, website="instagram", media_type="video")
    clear_attempt()

    begin_attempt("a2", "instagram", "video")
    record_error(get_db, resolver="yt-dlp", error_type="extractor")
    record_terminal_failure(get_db, resolver="yoinku")
    clear_attempt()

    data = get_monitor_data(get_db, days=1)
    assert data["total_attempts"] == 2
    assert data["successful"] == 1
    assert data["terminal_failures"] == 1

    cobalt = next(
        row for row in data["resolver_outcomes"]
        if row["resolver"] == "cobalt"
    )
    assert cobalt["recovered"] == 1

    assert data["recovery"] == [
        {"transition": "yt-dlp → cobalt", "count": 1}
    ]


def test_clear_attempt_prevents_legacy_success_attribution():
    holder = {"conn": sqlite3.connect(":memory:")}
    holder["conn"].row_factory = sqlite3.Row

    def get_db():
        class _Conn:
            def __getattr__(self, name):
                return getattr(holder["conn"], name)

            def close(self):
                pass

        return _Conn()

    begin_attempt("a1", "youtube", "video")
    clear_attempt()
    record_success(get_db, website="youtube", media_type="video")

    data = get_monitor_data(get_db, days=1)
    assert data["total_attempts"] == 0
