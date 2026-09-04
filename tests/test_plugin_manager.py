import os
import unittest
from unittest.mock import Mock, patch

from plugins.manager import PluginManager, PluginSpec, plugins_enabled


class PluginManagerTests(unittest.TestCase):
    def test_disabled_manager_has_no_side_effects(self):
        app = Mock()
        manager = PluginManager(app, enabled=False)
        self.assertEqual(manager.discover(), 0)
        self.assertEqual(manager.activate(), 0)
        app.bot_data.__setitem__.assert_not_called()

    def test_duplicate_plugin_names_are_rejected(self):
        manager = PluginManager(Mock(), enabled=True)
        spec = PluginSpec(name="demo")
        self.assertTrue(manager.register(spec))
        self.assertFalse(manager.register(spec))
        self.assertEqual(len(manager.plugins), 1)

    def test_plugin_activation_is_isolated(self):
        app = Mock()
        manager = PluginManager(app, enabled=True)
        manager.register(PluginSpec(name="broken", register=lambda _: (_ for _ in ()).throw(RuntimeError())))
        self.assertEqual(manager.activate(), 0)
        self.assertEqual(manager.errors["broken"], "RuntimeError")

    def test_environment_switch_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(plugins_enabled())
        with patch.dict(os.environ, {"ALIBOT_PLUGINS_ENABLED": "1"}, clear=True):
            self.assertTrue(plugins_enabled())


if __name__ == "__main__":
    unittest.main()
