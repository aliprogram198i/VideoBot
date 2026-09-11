import sqlite3

from plugins import admin_image_ai as ai


def test_image_ai_storage_is_separate(tmp_path, monkeypatch):
    root = tmp_path / "image-ai"
    monkeypatch.setenv("IMAGE_AI_DATA_DIR", str(root))
    ai._ensure_schema()

    assert (root / "image_ai.db").exists()
    conn = sqlite3.connect(root / "image_ai.db")
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()

    assert "image_ai_instructions" in tables
    assert "image_ai_examples" in tables
    assert "image_ai_jobs" in tables
    assert "users" not in tables
    assert "downloads" not in tables


def test_learning_instruction_is_bounded_and_used(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_AI_DATA_DIR", str(tmp_path / "image-ai"))
    ai._ensure_schema()
    ai._save_instruction("  حافظ على الهوية البرتقالية ولا تضف عناصر غير مطلوبة.  ")

    prompt = ai._build_prompt("احذف الخلفية فقط")
    assert "حافظ على الهوية البرتقالية" in prompt
    assert "احذف الخلفية فقط" in prompt


def test_extract_image_bytes_supports_inline_bytes():
    class Inline:
        data = b"PNGDATA"

    class Part:
        inline_data = Inline()

    class Response:
        parts = [Part()]

    assert ai._extract_image_bytes(Response()) == b"PNGDATA"


def test_empty_provider_fails_closed(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai._client() is None
