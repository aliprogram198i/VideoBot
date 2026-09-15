Dependency audit 2026-09-15

- python-telegram-bot: 22.8 — current PyPI release verified.
- yt-dlp-ejs: 0.8.0 — current PyPI release verified.
- yt-dlp-threads: 0.1.0 — current PyPI release verified.
- google-genai: 2.23.0 — newer than pinned 2.22.0.
- Playwright: 1.62.0 — newer than pinned 1.55.0.
- yt-dlp: pinned 2026.8.19; current stable was not safely established from authoritative release data in this audit, so it is intentionally not changed.

Decision: update only google-genai and Playwright. Do not move yt-dlp to a prerelease/nightly build merely to chase KRX18; KRX18 currently fails before candidate extraction, so this is not evidence of a yt-dlp defect.
