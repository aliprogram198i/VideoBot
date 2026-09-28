from pathlib import Path


BOT = Path("bot.py").read_text(encoding="utf-8")


def test_yoinku_negotiates_alternate_formats_after_422():
    assert "format_candidates = [selected_format]" in BOT
    assert '"format_candidates": format_candidates[:10]' in BOT
    assert "max_attempts = min(3, max(1, len(format_candidates)))" in BOT
    assert "result = await asyncio.to_thread(fetch, format_id)" in BOT
    assert 'diagnostics["format_id"] = format_id' in BOT


def test_yoinku_keeps_one_bounded_deadline_for_negotiation_and_download():
    assert "fetch_deadline = (" in BOT
    assert BOT.count("fetch_deadline = (") == 1
    assert "remaining_time = fetch_deadline - time.monotonic()" in BOT


def test_yoinku_does_not_repeat_same_format_when_alternatives_exist():
    assert "format_candidates = list(dict.fromkeys(" in BOT
    assert "if item[1] != selected_format" in BOT
    assert "if attempt < max_attempts - 1:" in BOT
