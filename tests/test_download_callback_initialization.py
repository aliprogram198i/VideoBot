from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_download_callback_preserves_instagram_image_artifact_before_shared_recovery():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    resolver = 'from downloader.instagram_image import download_instagram_image'
    shared_branch = 'instagram_access_blocked = ('
    reset = 'smart_file = None\n            smart_diagnostics = {'
    assert resolver in source
    assert source.index(resolver) < source.index(shared_branch)
    assert reset not in source[source.index(shared_branch):]


def test_youtube_skip_path_has_explicit_smart_diagnostics():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert '"skipped": "youtube_smart_extraction_not_applicable"' in source


def test_download_callback_initializes_total_parts_before_shared_delivery():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    marker = '    total_parts = 1\n    attempt_number = 1'
    assert marker in source


def test_download_callback_initializes_all_diagnostics_before_primary_download():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    init = '    smart_file = None\n    smart_diagnostics = {'
    try_marker = '    try:\n\n        output_template = os.path.join('
    assert init in source
    assert source.index(init) < source.index(try_marker)
    for name in ("relay_diagnostics", "graphql_diagnostics", "cobalt_diagnostics", "fallback_diagnostics", "yoinku_diagnostics"):
        assert f'    {name} = {{}}' in source

def test_smart_extraction_cannot_overwrite_admitted_instagram_image():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    marker = 'smart_file, smart_diagnostics = await download_with_smart_extraction('
    assert marker in source
    before = source[source.index(marker)-250:]
    assert "elif not smart_file:" in before
