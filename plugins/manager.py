"""Safe, dependency-free internal plugin manager for AliBot.

Design goals:
- zero changes to existing handlers unless a plugin opts in;
- deterministic loading of built-in plugins;
- duplicate-name protection;
- failure isolation so one optional plugin cannot stop the bot;
- explicit environment switch for enabling optional plugins.
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable

logger = logging.getLogger(__name__)

PLUGIN_ENV = "ALIBOT_PLUGINS_ENABLED"
PLUGIN_PACKAGE = __name__.rsplit(".", 1)[0]


@dataclass(frozen=True)
class PluginSpec:
    """Immutable description of one internal plugin."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    enabled: bool = True
    register: Callable[[Any], None] | None = field(default=None, repr=False, compare=False)


class PluginManager:
    """Own plugin discovery and registration without owning bot state."""

    def __init__(self, app: Any, *, enabled: bool = False) -> None:
        self.app = app
        self.enabled = enabled
        self.plugins: dict[str, PluginSpec] = {}
        self.errors: dict[str, str] = {}

    def register(self, spec: PluginSpec) -> bool:
        """Register a plugin spec; duplicate names are rejected safely."""
        if not spec.name or spec.name in self.plugins:
            return False
        self.plugins[spec.name] = spec
        return True

    def load_module(self, module: ModuleType) -> bool:
        """Load a module exposing ``get_plugin`` or ``PLUGIN``."""
        try:
            factory = getattr(module, "get_plugin", None)
            spec = factory() if callable(factory) else getattr(module, "PLUGIN", None)
            if not isinstance(spec, PluginSpec):
                return False
            return self.register(spec)
        except Exception as exc:
            name = getattr(module, "__name__", "unknown")
            self.errors[name] = type(exc).__name__
            logger.exception("AliBot plugin discovery failed: %s", name)
            return False

    def discover(self) -> int:
        """Discover plugins deterministically from the internal package."""
        if not self.enabled:
            return 0

        loaded = 0
        try:
            package = importlib.import_module(PLUGIN_PACKAGE)
        except Exception as exc:
            self.errors[PLUGIN_PACKAGE] = type(exc).__name__
            logger.exception("AliBot plugin package could not be loaded")
            return 0

        modules = sorted(pkgutil.iter_modules(package.__path__), key=lambda item: item.name)
        for module_info in modules:
            if module_info.name.startswith("_") or module_info.name == "manager":
                continue
            module_name = f"{PLUGIN_PACKAGE}.{module_info.name}"
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                self.errors[module_name] = type(exc).__name__
                logger.exception("AliBot optional plugin import failed: %s", module_name)
                continue
            loaded += int(self.load_module(module))
        return loaded

    def activate(self) -> int:
        """Register enabled plugins. Individual failures are isolated."""
        if not self.enabled:
            return 0

        activated = 0
        for name in sorted(self.plugins):
            spec = self.plugins[name]
            if not spec.enabled or spec.register is None:
                continue
            try:
                spec.register(self.app)
                activated += 1
                logger.info("AliBot plugin activated: %s v%s", spec.name, spec.version)
            except Exception as exc:
                self.errors[name] = type(exc).__name__
                logger.exception("AliBot plugin activation failed: %s", name)
        return activated

    def snapshot(self) -> dict[str, Any]:
        """Return safe diagnostics; never expose secrets or runtime objects."""
        return {
            "enabled": self.enabled,
            "plugins": [
                {
                    "name": spec.name,
                    "version": spec.version,
                    "description": spec.description,
                    "enabled": spec.enabled,
                }
                for spec in self.plugins.values()
            ],
            "errors": dict(self.errors),
        }


def plugins_enabled() -> bool:
    """Read an explicit opt-in switch; disabled is the safe default."""
    value = os.getenv(PLUGIN_ENV, "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def load_plugins(app: Any) -> PluginManager:
    """Create, discover and activate optional plugins safely."""
    manager = PluginManager(app, enabled=plugins_enabled())
    manager.discover()
    manager.activate()
    return manager
