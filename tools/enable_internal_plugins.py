"""Idempotently inject the internal plugin manager into bot.py during build."""

from pathlib import Path

TARGET = Path("bot.py")
IMPORT = "from plugins import load_plugins\n"
CALL = "    plugin_manager = load_plugins(app)\n"
MARKER = "    # ========================================================\n    # معالج الأخطاء\n"


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")

    if IMPORT not in text:
        anchor = "from telegram.request import HTTPXRequest\n"
        if anchor not in text:
            raise SystemExit("bot.py import anchor not found")
        text = text.replace(anchor, anchor + IMPORT, 1)

    if CALL not in text:
        if MARKER not in text:
            raise SystemExit("bot.py application anchor not found")
        text = text.replace(MARKER, CALL + "\n" + MARKER, 1)

    TARGET.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
