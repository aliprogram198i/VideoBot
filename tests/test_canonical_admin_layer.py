import re

from telegram.ext import CallbackQueryHandler

from plugins.admin_control_center import admin_keyboard
import plugins.admin_layer_v2 as admin_layer_v2


OWNER_ID = 1486412391


class FakeApp:
    def __init__(self):
        self.handlers = {}

    def add_handler(self, handler, group=0):
        self.handlers.setdefault(group, []).append(handler)


def pattern(handler):
    value = getattr(handler, "pattern", None)
    return getattr(value, "pattern", None) or (value if isinstance(value, str) else "")


def _register_without_runtime_db_migration(monkeypatch):
    """Keep ownership tests focused on handler composition, not DB migration."""
    monkeypatch.setattr(
        admin_layer_v2,
        "register_download_log_enrichment",
        lambda bot_module: None,
    )


def _build_app(monkeypatch):
    _register_without_runtime_db_migration(monkeypatch)
    app = FakeApp()

    class Bot:
        ADMIN_ID = OWNER_ID
        get_db = staticmethod(lambda: None)

    admin_layer_v2.register_admin_layer(app, Bot, OWNER_ID)
    return app


def test_canonical_admin_layer_has_single_exact_callback_owner(monkeypatch):
    app = _build_app(monkeypatch)
    patterns = [
        pattern(handler)
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, CallbackQueryHandler) and pattern(handler)
    ]
    assert len(patterns) == len(set(patterns))


def test_user_workspace_has_no_legacy_users_page_owner(monkeypatch):
    app = _build_app(monkeypatch)
    patterns = [
        pattern(handler)
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, CallbackQueryHandler)
    ]
    assert sum(bool(re.fullmatch(r"\^admin_users_page_0\$", p)) for p in patterns) == 1
    assert not any(p == r"^admin_users_page_\d+$" for p in patterns)


def test_all_top_level_admin_buttons_have_registered_owner(monkeypatch):
    app = _build_app(monkeypatch)
    patterns = [
        pattern(handler)
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, CallbackQueryHandler) and pattern(handler)
    ]
    missing = []
    for row in admin_keyboard().inline_keyboard:
        for button in row:
            data = button.callback_data
            if data and not any(re.match(p, data) for p in patterns):
                missing.append(data)
    assert not missing, f"Missing canonical admin callback owners: {missing}"
