from pathlib import Path

STUDIO = Path("plugins/media_studio.py").read_text(encoding="utf-8")


def test_studio_prevents_duplicate_operations():
    assert "STUDIO_LOCK_KEY = \"media_studio_operation\"" in STUDIO
    assert "if _studio_busy(context, token, action):" in STUDIO
    assert "_studio_release(context)" in STUDIO


def test_studio_has_live_processing_heartbeat():
    assert "async def _studio_heartbeat(" in STUDIO
    assert "asyncio.create_task(" in STUDIO
    assert "time.monotonic()" in STUDIO


def test_studio_uses_existing_telegram_optimizer_for_large_results():
    assert "from downloader.user_experience import optimize_video_for_telegram" in STUDIO
    assert "optimize_video_for_telegram(" in STUDIO
    assert "max_bytes=MAX_RESULT_BYTES" in STUDIO


def test_studio_media_type_controls_applicable_actions():
    assert 'media_type: str | None = "video"' in STUDIO
    assert 'if media_type == "audio":' in STUDIO
    assert 'elif media_type in {"video", "unknown"}:' in STUDIO


def test_studio_preserves_bounded_version_history_and_undo():
    assert "MAX_STUDIO_HISTORY = 3" in STUDIO
    assert 'STUDIO_HISTORY_KEY = "media_studio_history"' in STUDIO
    assert 'callback_data=f"studio:undo:{token}"' in STUDIO
    assert 'action == "undo"' in STUDIO


def test_link_info_reflects_current_studio_version():
    assert "def _studio_info_message(" in STUDIO
    assert '"studio_current": True' in STUDIO
    assert "current_version" in STUDIO


def test_studio_callback_ack_is_parallel_and_submenus_do_not_double_answer():
    assert "answer_task = asyncio.create_task(query.answer())" in STUDIO
    assert "await asyncio.gather(" in STUDIO
    assert "await query.answer(prompt)" not in STUDIO
    assert "await _run_action(update, context, token, action, value)\n    await answer_task" in STUDIO
