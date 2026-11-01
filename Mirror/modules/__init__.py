"""Feature modules. ``load()`` imports the ones that register Telegram handlers."""

import importlib

HANDLER_MODULES = ("Mirror.core.ui", "Mirror.modules.status", "Mirror.modules.mirror", "Mirror.modules.usettings",
                   "Mirror.modules.bsettings", "Mirror.modules.rss", "Mirror.modules.search", "Mirror.modules.admin")


def load() -> None:
    for name in HANDLER_MODULES:
        importlib.import_module(name)
