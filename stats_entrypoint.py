"""Backward-compatible Railway entrypoint.

The canonical production startup logic lives in ``entrypoint.py``.
This module intentionally delegates to it so an older Railway Start Command
that still references ``stats_entrypoint.py`` cannot break the service.
"""

from entrypoint import main


if __name__ == "__main__":
    main()
