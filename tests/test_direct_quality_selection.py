from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


def _show_main_menu_source():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "show_main_menu":
            return ast.get_source_segment(BOT.read_text(encoding="utf-8"), node)
    raise AssertionError("show_main_menu was not found")


def test_link_panel_exposes_download_qualities_directly():
    source = _show_main_menu_source()

    expected = {
        'callback_data="video_best"',
        'callback_data="video_1080"',
        'callback_data="video_720"',
        'callback_data="video_480"',
        'callback_data="video_360"',
        'callback_data="audio_best"',
        'callback_data="audio_320"',
        'callback_data="audio_256"',
        'callback_data="audio_192"',
        'callback_data="audio_128"',
    }

    assert expected.issubset(set(
        line.strip()
        for line in source.splitlines()
        if "callback_data=" in line
    ))

    assert 'callback_data="video_menu"' not in source
    assert 'callback_data="audio_menu"' not in source


def test_post_download_resize_is_not_the_download_quality_panel():
    source = _show_main_menu_source()

    assert 'callback_data="post_download"' in source
    assert 'callback_data="video_720"' in source
    assert 'callback_data="audio_320"' in source
