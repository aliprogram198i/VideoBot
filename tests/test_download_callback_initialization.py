from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_download_callback_initializes_smart_diagnostics_before_branch():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    marker = 'smart_file = None\n            smart_diagnostics = {'
    branch = 'instagram_access_blocked = ('
    assert marker in source
    assert source.index(marker) < source.index(branch)


def test_youtube_skip_path_has_explicit_diagnostics():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert '"skipped": "youtube_smart_extraction_not_applicable"' in source
