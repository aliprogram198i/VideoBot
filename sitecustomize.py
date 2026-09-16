"""Runtime bootstrap for AliBot's canonical admin layer.

Python imports ``sitecustomize`` during interpreter startup when it is present
on ``sys.path``. We use that hook only to install the existing canonical
``admin_layer_v2`` immediately before the bot starts polling. This avoids
rewriting the large legacy ``bot.py`` registration block while allowing the
canonical layer to remove duplicate legacy admin handlers first.
"""

import logging

_LOG = logging.getLogger(__name__)


def _install_admin_runtime_guard():
    try:
        from telegram.ext import Application
    except Exception:
        return

    original = Application.run_polling
    if getattr(original, "_alibot_admin_guard", False):
        return

    def guarded_run_polling(self, *args, **kwargs):
        if not getattr(self, "_alibot_admin_layer_installed", False):
            try:
                import __main__ as bot_module
                from plugins.admin_layer_v2 import register_admin_layer

                admin_id = getattr(bot_module, "ADMIN_ID", None)
                if admin_id is None:
                    raise RuntimeError("ADMIN_ID is missing")

                register_admin_layer(self, bot_module, int(admin_id))
                self._alibot_admin_layer_installed = True
            except Exception:
                _LOG.exception("AliBot canonical admin layer installation failed")
                raise

        return original(self, *args, **kwargs)

    guarded_run_polling._alibot_admin_guard = True
    Application.run_polling = guarded_run_polling


_install_admin_runtime_guard()
