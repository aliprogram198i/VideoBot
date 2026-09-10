import re

from telegram.ext import CallbackQueryHandler

import plugins.admin_layer_v2 as admin_layer_v2


class FakeApp:
    def __init__(self):
        self.handlers = {}

    def add_handler(self, handler, group=0):
        self.handlers.setdefault(group, []).append(handler)


def _pattern(handler):
    value = getattr(handler, "pattern", None)
    return getattr(value, "pattern", None) or (value if isinstance(value, str) else "")


def test_fallback_intelligence_has_one_canonical_owner(monkeypatch):
    monkeypatch.setattr(admin_layer_v2, "init_admin_control_center", lambda get_db, owner_id: None)
    monkeypatch.setattr(admin_layer_v2, "register_download_log_enrichment", lambda bot_module: None)
    app = FakeApp()

    class Bot:
        get_db = staticmethod(lambda: None)

    admin_layer_v2.register_admin_layer(app, Bot, 1)
    handlers = [
        handler
        for group_handlers in app.handlers.values()
        for handler in group_handlers
        if isinstance(handler, CallbackQueryHandler) and _pattern(handler)
    ]
    matches = [
        handler for handler in handlers
        if re.fullmatch(_pattern(handler), "admin_fallback_intelligence")
    ]
    assert len(matches) == 1
