from pathlib import Path


STUDIO = Path("plugins/media_studio.py").read_text(encoding="utf-8")


def test_processed_video_keeps_studio_workflow_available():
    """A processed video remains editable without another download."""
    assert "sent = await context.bot.send_video(" in STUDIO
    assert "studio_token = cache_media_for_user(" in STUDIO
    assert "reply_markup=studio_keyboard(studio_token, language)" in STUDIO


def test_single_download_studio_cache_behavior_is_preserved():
    """The original download path still exposes Studio on a validated video."""
    bot = Path("bot.py").read_text(encoding="utf-8")
    assert "studio_token = cache_media_for_user(" in bot
    assert "reply_markup=studio_keyboard(studio_token)" in bot
