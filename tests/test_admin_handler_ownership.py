import re
import sqlite3

from telegram.ext import CallbackQueryHandler

from plugins.admin_control_center import admin_keyboard, register_admin_control_center
from plugins.admin_layer import _remove_legacy_admin_callback_handlers


OWNER_ID = 1486412391


def _get_db_factory():
    def get_db():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn
    return get_db


class FakeApp:
    def __init__(self, handlers=None):
        self.handlers = handlers or {}

    def add_handler(self, handler, group=0):
        self.handlers.setdefault(group, []).append(handler)


def _pattern(handler):
    pattern = getattr(handler, "pattern", None)
    return getattr(pattern, "pattern", None) or (pattern if isinstance(pattern, str) else "")


def test_retired_legacy_admin_callbacks_are_removed():
    legacy_patterns = [
        r"^admin_dashboard_(1|7|30)$",
        r"^admin_broadcast$",
        r"^admin_storage$",
        r"^ban_",
        r"^unban_",
        r"^delete_user_",
        r"^message_user_",
        r"^admin_search$",
        r"^admin_ai$",
    ]
    app = FakeApp({0: [CallbackQueryHandler(lambda *_: None, pattern=p) for p in legacy_patterns]})
    removed = _remove_legacy_admin_callback_handlers(app)
    assert removed == len(legacy_patterns)
    assert all(_pattern(h) not in legacy_patterns for h in app.handlers[0])


def test_legacy_user_detail_callback_is_removed_without_touching_user_features():
    patterns = [
        r"^user_12345$",
        r"^user_single_download$",
        r"^user_batch_prompt$",
        r"^user_history$",
        r"^user_history_pick_\d+$",
    ]
    app = FakeApp({0: [CallbackQueryHandler(lambda *_: None, pattern=p) for p in patterns]})

    removed = _remove_legacy_admin_callback_handlers(app)

    remaining = {_pattern(handler) for handlers in app.handlers.values() for handler in handlers}
    assert removed == 1
    assert r"^user_12345$" not in remaining
    assert r"^user_single_download$" in remaining
    assert r"^user_batch_prompt$" in remaining
    assert r"^user_history$" in remaining
    assert r"^user_history_pick_\d+$" in remaining


def test_new_admin_registration_has_no_duplicate_exact_callback_owners():
    app = FakeApp()
    register_admin_control_center(app, _get_db_factory(), OWNER_ID)
    patterns = [_pattern(h) for handlers in app.handlers.values() for h in handlers if isinstance(h, CallbackQueryHandler)]
    assert len(patterns) == len(set(patterns))


def test_top_level_admin_buttons_have_registered_owners():
    app = FakeApp()
    register_admin_control_center(app, _get_db_factory(), OWNER_ID)
    patterns = [_pattern(h) for handlers in app.handlers.values() for h in handlers if isinstance(h, CallbackQueryHandler)]
    callback_data = {
        button.callback_data
        for row in admin_keyboard().inline_keyboard
        for button in row
        if button.callback_data
    }
    externally_owned = {"recover_users_menu"}
    missing = []
    for data in sorted(callback_data - externally_owned):
        if not any(re.match(pattern, data) for pattern in patterns if pattern):
            missing.append(data)
    assert not missing, f"Admin buttons without a registered owner: {missing}"
