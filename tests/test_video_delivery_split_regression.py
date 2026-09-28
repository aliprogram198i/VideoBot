from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


def test_oversized_video_delivery_splits_without_forced_optimization():
    source = BOT.read_text(encoding="utf-8")

    assert "optimize_video_for_telegram" not in source
    assert "split_video_for_telegram(" in source
    assert "split_limit_bytes = 47 * 1024 * 1024" in source
    assert 'media_type="video"' in source


def test_every_split_video_part_gets_smart_studio_keyboard():
    source = BOT.read_text(encoding="utf-8")

    split_block_start = source.index("# فيديو أكبر من الحد: تقسيمه بدون إعادة ترميز.")
    split_block_end = source.index("# حفظ التحميل", split_block_start)
    split_block = source[split_block_start:split_block_end]

    assert "sent_part = await context.bot.send_video(" in split_block
    assert "cache_media_for_user(" in split_block
    assert "studio_keyboard(" in split_block
    assert 'language=language' in split_block
    assert 'media_type="video"' in split_block
