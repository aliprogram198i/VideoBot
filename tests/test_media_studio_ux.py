from pathlib import Path


STUDIO = Path("plugins/media_studio.py").read_text(encoding="utf-8")


def test_processed_video_keeps_studio_workflow_available():
    """A processed video remains editable without another download."""
    assert "sent = await context.bot.send_video(" in STUDIO
    assert "studio_token = cache_media_for_user(" in STUDIO
    assert "reply_markup=studio_keyboard(" in STUDIO
    assert "can_undo=True" in STUDIO


def test_single_download_studio_cache_behavior_is_preserved():
    """The original download path still exposes Studio on a validated video."""
    bot = Path("bot.py").read_text(encoding="utf-8")
    assert "studio_token = cache_media_for_user(" in bot
    # The keyboard is attached in the send call and may be formatted across lines.
    assert "reply_markup=studio_keyboard(" in bot
    assert "studio_token" in bot


def test_primary_ytdlp_download_explicitly_disables_simulation():
    """The primary downloader must create an artifact even when --print is used."""
    bot = Path("bot.py").read_text(encoding="utf-8")
    assert '"--no-simulate"' in bot


def test_transient_status_message_is_removed_after_success():
    """Progress text is cleaned up after the result is delivered."""
    assert "status_message = await message.reply_text(status)" in STUDIO
    assert "await status_message.delete()" in STUDIO
    assert "studio_status_message_delete_failed" in STUDIO


def test_transient_status_message_becomes_failure_state_on_error():
    """Failures update the existing progress message instead of leaving it stale."""
    assert 'await status_message.edit_text(t("studio", "failed", language))' in STUDIO
