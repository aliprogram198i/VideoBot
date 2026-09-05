"""Production entrypoint guard for AliBot.

Prevents accidental duplicate bot processes inside the same runtime/container.
It does not replace Telegram-side token ownership checks.
"""

import fcntl
import os
import sys

LOCK_PATH = "/tmp/alibot-single-instance.lock"


def main() -> None:
    lock_file = open(LOCK_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("❌ AliBot startup blocked: another local instance is already running.", flush=True)
        sys.exit(75)

    os.execv(sys.executable, [sys.executable, "-u", "bot.py"])


if __name__ == "__main__":
    main()
