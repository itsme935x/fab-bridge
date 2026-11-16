"""Minimal `bpy` stub for headless tests.

This does not emulate Blender — it only provides the attributes the addon
touches at import/register time, so wiring bugs surface without an install.
"""

from __future__ import annotations

import sys
import types


class _FakeTimers:
    def __init__(self):
        self.registered = []

    def register(self, fn, first_interval=0.0, persistent=False):
        self.registered.append(fn)

    def is_registered(self, fn):
        return fn in self.registered

    def unregister(self, fn):
        if fn in self.registered:
            self.registered.remove(fn)


class _FakeAddons(dict):
    pass


def install_stub(config_dir: str):
    bpy = types.ModuleType("bpy")

    bpy_types = types.ModuleType("bpy.types")

    class _Base:
        pass

    class AddonPreferences(_Base):
        pass

    class Operator(_Base):
        pass

    class Panel(_Base):
        pass

    class Menu(_Base):
        pass

    class Scene(_Base):
        pass

    bpy_types.AddonPreferences = AddonPreferences
    bpy_types.Operator = Operator
    bpy_types.Panel = Panel
    bpy_types.Menu = Menu
    bpy_types.Scene = Scene
    bpy_types.PropertyGroup = _Base

    bpy_props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "IntProperty", "StringProperty", "EnumProperty",
                 "FloatProperty", "CollectionProperty", "PointerProperty"):
        setattr(bpy_props, name, lambda *a, **k: None)

    bpy_app = types.ModuleType("bpy.app")
    bpy_app.timers = _FakeTimers()
    bpy_app.online_access = True
    bpy_app.background = False
    bpy_app.version_string = "4.2.0"
    bpy_app.version = (4, 2, 0)

    bpy_utils = types.ModuleType("bpy.utils")
    bpy_utils._classes = []

    def register_class(cls):
        bpy_utils._classes.append(cls)

    def unregister_class(cls):
        if cls in bpy_utils._classes:
            bpy_utils._classes.remove(cls)

    bpy_utils.register_class = register_class
    bpy_utils.unregister_class = unregister_class
    bpy_utils.user_resource = lambda kind, *a, **k: config_dir

    bpy_context = types.ModuleType("bpy.context")
    bpy_context.preferences = types.SimpleNamespace(addons=_FakeAddons())
    bpy_context.window_manager = types.SimpleNamespace(windows=[], clipboard="")
    bpy_context.window = None

    # `bpy.utils.previews` is a real submodule, not an attribute of bpy.utils.
    bpy_previews = types.ModuleType("bpy.utils.previews")

    def _new_collection():
        return types.SimpleNamespace(load=lambda *a, **k: types.SimpleNamespace(icon_id=1),
                                     remove=lambda *a, **k: None)

    bpy_previews.new = _new_collection
    bpy_previews.remove = lambda *a, **k: None
    bpy_utils.previews = bpy_previews

    bpy.types = bpy_types
    bpy.props = bpy_props
    bpy.app = bpy_app
    bpy.utils = bpy_utils
    bpy.context = bpy_context

    modules = {
        "bpy": bpy,
        "bpy.types": bpy_types,
        "bpy.props": bpy_props,
        "bpy.app": bpy_app,
        "bpy.utils": bpy_utils,
        "bpy.utils.previews": bpy_previews,
        "bpy.context": bpy_context,
    }
    sys.modules.update(modules)
    return bpy


def uninstall_stub():
    for name in ("bpy.types", "bpy.props", "bpy.app", "bpy.utils.previews", "bpy.utils",
                 "bpy.context", "bpy"):
        sys.modules.pop(name, None)
    for name in list(sys.modules):
        if name == "fab_bridge" or name.startswith("fab_bridge."):
            sys.modules.pop(name, None)
