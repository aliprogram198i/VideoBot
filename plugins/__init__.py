"""Internal AliBot plugin system.

Plugins are optional extensions around the existing bot runtime.  The
core downloader and handlers remain unchanged unless a plugin explicitly
registers something with the application.
"""

from .manager import PluginManager, PluginSpec, load_plugins

__all__ = ["PluginManager", "PluginSpec", "load_plugins"]
