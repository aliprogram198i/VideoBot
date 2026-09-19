from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_smart_search_has_canonical_intent_gate():
    source = (ROOT / "plugins" / "smart_search_pro.py").read_text(encoding="utf-8")
    assert "is_smart_search_intent" in source
    assert "MessageHandler(filters.TEXT & ~filters.COMMAND" in source


def test_entrypoint_stops_admin_workflow_propagation():
    source = (ROOT / "entrypoint.py").read_text(encoding="utf-8")
    assert "ApplicationHandlerStop" in source
    assert "is_smart_search_intent" in source


def test_canonical_download_event_is_used_by_data_layer():
    source = (ROOT / "data_layer.py").read_text(encoding="utf-8")
    assert "DownloadEvent(" in source
    assert "job_id=job_id" in source
    assert "attempt_id=attempt_id" in source


def test_protected_identity_gate_is_canonical():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "enforce_source_identity(" in source
    assert "candidate_matches_telegram_source" in source
    assert "candidate_matches_instagram_source" in source
