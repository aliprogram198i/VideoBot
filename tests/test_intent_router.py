from types import SimpleNamespace

from intent_router import Intent, classify_update


def update(text, user_id=10):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(text=text),
    )


class Ctx:
    def __init__(self, data=None):
        self.user_data = data or {}


def test_url_is_not_smart_search():
    assert classify_update(update("https://example.com/video"), Ctx()) is Intent.URL


def test_plain_text_is_smart_search():
    assert classify_update(update("song name"), Ctx()) is Intent.SMART_SEARCH


def test_pending_download_owns_text_flow():
    assert classify_update(update("another message"), Ctx({"video_url": "https://example.com/v"})) is Intent.PENDING_DOWNLOAD


def test_admin_workflow_owns_text_flow():
    assert classify_update(update("broadcast body"), Ctx({"rich_broadcast_waiting": True}), admin_id=10) is Intent.ADMIN_WORKFLOW


def test_command_is_never_search():
    assert classify_update(update("/start"), Ctx()) is Intent.COMMAND
