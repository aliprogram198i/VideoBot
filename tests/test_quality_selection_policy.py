import ast
from pathlib import Path

BOT = Path(__file__).resolve().parents[1] / "bot.py"


def _format_options():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    values = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "format_option" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            values.append(node.value.value)
    return values


def test_selected_video_qualities_have_hard_height_ceiling():
    values = _format_options()
    expected = {
        1080: "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        720: "bestvideo*[height<=720]+bestaudio/best[height<=720]",
        480: "bestvideo*[height<=480]+bestaudio/best[height<=480]",
        360: "bestvideo*[height<=360]+bestaudio/best[height<=360]",
    }
    for selector in expected.values():
        assert selector in values
        assert selector + "/best" not in values


def test_quality_callbacks_cover_all_requested_video_levels_and_mp3():
    source = BOT.read_text(encoding="utf-8")
    for choice in ("video_1080", "video_720", "video_480", "video_360"):
        assert f'choice == "{choice}"' in source
    for choice, bitrate in (("audio_320", "320K"), ("audio_256", "256K"), ("audio_192", "192K"), ("audio_128", "128K")):
        assert f'choice == "{choice}"' in source
        assert f'audio_quality = "{bitrate}"' in source
    assert '"--audio-format"' in source
    assert '"mp3"' in source


def test_quality_menu_callbacks_are_reachable():
    source = BOT.read_text(encoding="utf-8")
    assert 'callback_data="video_menu"' in source
    assert 'callback_data="audio_menu"' in source
    assert 'pattern=r"^(video_menu|audio_menu|video_|audio_|main_menu|post_download|retry_download)$"' in source
