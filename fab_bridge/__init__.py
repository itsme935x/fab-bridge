"""Fab Bridge — browse and import Fab / Quixel Megascans assets.

Blender 4.2+ extension (`blender_manifest.toml` is the only metadata path).
Entry point only: wiring, ordering, teardown.

Submodules are imported inside `register()`, not at module import time, so the
pure-python parts of the addon (models, store, code parsing) can be exercised
without a Blender install.
"""

from __future__ import annotations

ADDON_ID = __package__ or "fab_bridge"
VERSION = "0.1.0"

_registered = False


def register() -> None:
    global _registered

    import bpy

    from .auth import manager as auth_manager
    from .core import log, platform
    from .download import manager as downloads
    from .ui import ops, panel, prefs, props

    prefs.register(ADDON_ID)
    settings = prefs.get()
    logger = log.setup(settings.log_level if settings else "info")
    logger.info("Fab Bridge %s registering", VERSION)

    props.register()

    auth_manager.MANAGER.subscribe(_on_auth_change)
    auth_manager.MANAGER.setup()

    # The download layer is bpy-free; the import step is not, so it is injected
    # here and only imported when a job actually reaches it.
    downloads.IMPORT_HOOK = _import_download

    ops.register()
    panel.register()

    # Main-thread UI pump: deferred redraws and worker results land here.
    if not bpy.app.timers.is_registered(platform.pump):
        bpy.app.timers.register(platform.pump, first_interval=platform.PUMP_INTERVAL, persistent=True)

    _registered = True


def unregister() -> None:
    global _registered

    import bpy

    from .auth import manager as auth_manager
    from .core import log, platform, tasks
    from .download import manager as downloads
    from .ui import browser, ops, panel, prefs, previews, props

    auth_manager.MANAGER.unsubscribe(_on_auth_change)
    auth_manager.MANAGER.teardown()
    downloads.shutdown()

    if bpy.app.timers.is_registered(platform.pump):
        bpy.app.timers.unregister(platform.pump)

    browser.reset()
    tasks.clear()
    previews.clear()

    panel.unregister()
    ops.unregister()
    props.unregister()
    prefs.unregister()

    log.get().info("Fab Bridge unregistered")
    _registered = False


def _on_auth_change(_manager) -> None:
    from .core import platform

    platform.redraw()


def _import_download(job):
    """Main-thread import hook installed on `download.manager`."""
    from . import importer

    return importer.import_download(job)


if __name__ == "__main__":
    register()
