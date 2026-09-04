"""Core plugin registration for the internal extension layer.

This plugin intentionally adds no Telegram handlers. It only exposes a
stable runtime registry so future features can be introduced independently
without touching the existing downloader flow.
"""

from .manager import PluginSpec


PLUGIN = PluginSpec(
    name="core-runtime",
    version="1.0.0",
    description="Stable runtime registry for AliBot internal extensions.",
    register=lambda app: app.bot_data.setdefault("alibot_plugins", {}),
)
