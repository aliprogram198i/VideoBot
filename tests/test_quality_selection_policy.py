import ast
from pathlib import Path

BOT = Path(__file__).resolve().parents[1] / "bot.py"


def _quality_assignments():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "download_media")
    found = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "format_option":
                parent = next(
                    (n for n in ast.walk(fn) if isinstance(n, (ast.If, ast.Assign)) and node in getattr(n, "body", [])),
                    None,
                )
                found.setdefault(node.value.value, "")
    return found


def test_selected_video_qualities_have_hard_height_ceiling():
    source = BOT.read_text(encoding="utf-8")
    expected = {
        1080: "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        720: "bestvideo*[height<=720]+bestaudio/best[height<=720]",
        480: "bestvideo*[height<=480]+bestaudio/best[height<=480]",
        360: "bestvideo*[height<=360]+bestaudio/best[height<=360]",
    }
    for height, selector in expected.items():
        assert selector in source
        assert selector + "/best" not in source


def test_quality_callbacks_cover_all_requested_video_levels_and_mp3():
    source = BOT.read_text(encoding="utf-8")
    for choice in ("video_1080", "video_720", "video_480", "video_360"):
        assert f'choice == "{choice}"' in source
    for choice, bitrate in (("audio_320", "320K"), ("audio_256", "256K"), ("audio_192", "192K"), ("audio_128", "128K")):
        assert f'choice == "{choice}"' in source
        assert f'audio_quality = "{bitrate}"' in source
    assert '"--audio-format",\n                "mp3"' in source
