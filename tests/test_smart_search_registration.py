from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _runtime_source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_smart_search_has_one_runtime_registration_owner():
    bot = _runtime_source("bot.py")
    entrypoint = _runtime_source("entrypoint.py")

    assert bot.count("register_smart_search_pro(") == 1
    assert "register_smart_search_pro(" not in entrypoint


def test_entrypoint_keeps_admin_smart_search_handoff_without_registration():
    entrypoint = _runtime_source("entrypoint.py")
    assert 'importlib.import_module("plugins.smart_search_pro")._search_handler' in entrypoint
    assert "await register_smart_search_handler(update, context, bot_module)" in entrypoint
