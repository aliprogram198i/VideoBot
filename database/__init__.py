"""Database boundary for VideoBot-Next.

The legacy bot remains the compatibility caller during the staged refactor.
New code should depend on this package instead of importing sqlite3 directly.
"""

from .connection import Database, connect

__all__ = ["Database", "connect"]
