"""Addon preferences (SPEC.md §12.4).

Registered with the addon id supplied by the package `__init__`, so the same
class works whether the addon is loaded as an extension or from a dev folder.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from ..api import endpoints as ep

DEFAULT_LIBRARY = os.path.join(os.path.expanduser("~"), "Documents", "FabAssets")


def _refresh_query(**changes) -> None:
    """Re-run the current search with a changed preference, if browsing started."""
    from . import browser  # noqa: PLC0415 - avoid ui import at module scope

    if not browser.STATE.started:
        return
    browser.set_query(browser.STATE.query.replaced(**changes))


def _on_sort_changed(self, context) -> None:
    _refresh_query(sort=self.default_sort)


def _on_importable_changed(self, context) -> None:
    _refresh_query(importable=bool(self.importable_only))

QUALITY_ITEMS = (
    ("low", "Low (1K)", "1K maps / lowest LOD"),
    ("mid", "Mid (2K)", "2K maps"),
    ("high", "High (4K)", "4K maps"),
    ("raw", "Raw (8K)", "8K maps / raw scan mesh"),
)


class FABBRIDGE_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = "fab_bridge"

    library_root: StringProperty(
        name="Library Folder",
        description="Where downloaded assets are stored",
        subtype="DIR_PATH",
        default=DEFAULT_LIBRARY,
    )

    default_quality_surface: EnumProperty(name="Surface Quality", items=QUALITY_ITEMS, default="mid")
    default_quality_prop: EnumProperty(name="Prop Quality", items=QUALITY_ITEMS, default="high")
    default_quality_plant: EnumProperty(name="Plant Quality", items=QUALITY_ITEMS, default="mid")

    prefer_format: EnumProperty(
        name="Preferred Format",
        items=(
            ("auto", "Automatic", "glTF for props, FBX for plants, texture set for surfaces"),
            ("converted", "glTF (converted)", "Use Fab's converted-files when available"),
            ("fbx", "FBX", ""),
            ("gltf", "glTF (UE)", ""),
        ),
        default="auto",
    )

    flip_normals: EnumProperty(
        name="Normal Map Green",
        items=(
            ("auto", "Automatic", "Flip for FBX/UE sources, keep for converted glTF"),
            ("always", "Always flip", ""),
            ("never", "Never flip", ""),
        ),
        default="auto",
    )

    tiling_mode: EnumProperty(
        name="Tiling",
        items=(
            ("physical", "Physical Size", "Map scale from the asset's real-world size"),
            ("none", "None", "Plain 0-1 UVs"),
        ),
        default="physical",
    )

    displacement: BoolProperty(name="Use Displacement", default=True)
    add_subdivision: BoolProperty(
        name="Add Subdivision", description="Add a subdivision modifier for displacement", default=False
    )
    pack_images: BoolProperty(name="Pack Images", description="Pack textures into the .blend", default=False)
    keep_archives: BoolProperty(name="Keep ZIPs", default=False)

    max_workers: IntProperty(name="Parallel Downloads", default=3, min=1, max=4)
    cache_limit_gb: IntProperty(name="Cache Limit (GB)", description="0 = unlimited", default=0, min=0)

    write_asset_blend: BoolProperty(name="Write Asset Browser Files", default=True)
    import_all_variations: BoolProperty(name="Import All Plant Variations", default=False)
    # NOTE: `max_workers`, `cache_limit_gb`, `write_asset_blend` and
    # `import_all_variations` stay registered so existing configs keep loading,
    # but they are absent from `draw()`: nothing reads them yet, and a control
    # that changes nothing is worse than no control.

    thumbnail_columns: EnumProperty(
        name="Grid Columns",
        items=(("auto", "Auto", ""), ("1", "1", ""), ("2", "2", ""), ("3", "3", "")),
        default="auto",
    )
    hero_width: IntProperty(name="Preview Width", default=1920, min=320, max=2560)
    default_sort: EnumProperty(
        name="Sort",
        items=tuple((value, label, "") for value, label in ep.SORTS),
        default="-relevance",
        update=_on_sort_changed,
    )
    importable_only: BoolProperty(
        name="Only Show Importable",
        description="Hide listings with no format Blender can read",
        default=True,
        update=_on_importable_changed,
    )

    experimental_pkce: BoolProperty(
        name="Experimental: browser loopback login",
        description="Reserved for SPEC.md §6.3 Flow B — not wired yet, so it is not drawn",
        default=False,
    )
    # NOTE: kept registered so an existing config does not fail to load, but it
    # is deliberately absent from `draw()` until Flow B is validated against a
    # real Epic account. A toggle that changes nothing is worse than no toggle.

    auto_capture: BoolProperty(
        name="Catch the browser sign-in",
        description=(
            "Listen on https://localhost so Epic's redirect finishes sign-in by itself. "
            "Turn off to copy the code from Epic's page instead"
        ),
        default=True,
    )

    log_level: EnumProperty(
        name="Log Level",
        items=(("debug", "Debug", ""), ("info", "Info", ""), ("warning", "Warnings", ""), ("error", "Errors", "")),
        default="info",
    )

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Storage")
        box.prop(self, "library_root")
        box.prop(self, "keep_archives")

        box = layout.box()
        box.label(text="Defaults")
        box.prop(self, "default_quality_surface")
        box.prop(self, "default_quality_prop")
        box.prop(self, "default_quality_plant")
        box.prop(self, "prefer_format")

        box = layout.box()
        box.label(text="Import")
        box.prop(self, "flip_normals")
        box.prop(self, "tiling_mode")
        row = box.row()
        row.prop(self, "displacement")
        row.prop(self, "add_subdivision")
        box.prop(self, "pack_images")

        box = layout.box()
        box.label(text="Browser")
        box.prop(self, "default_sort")
        box.prop(self, "importable_only")
        row = box.row()
        row.prop(self, "thumbnail_columns")
        row.prop(self, "hero_width")

        box = layout.box()
        box.label(text="Advanced")
        box.prop(self, "log_level")
        box.prop(self, "auto_capture")


def register(addon_id: str) -> None:
    FABBRIDGE_AddonPreferences.bl_idname = addon_id
    bpy.utils.register_class(FABBRIDGE_AddonPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(FABBRIDGE_AddonPreferences)


def get(context=None):
    """Preferences for the running addon, or None if not registered."""
    ctx = context or bpy.context
    addon = ctx.preferences.addons.get(FABBRIDGE_AddonPreferences.bl_idname)
    return addon.preferences if addon else None
