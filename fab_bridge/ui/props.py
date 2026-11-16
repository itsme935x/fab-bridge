"""Scene properties owned by the addon (SPEC.md §12.2).

Only the search box needs one: filters live in `ui.browser.STATE` and the
persistent defaults in preferences. `TEXTEDIT_UPDATE` makes the update callback
fire per keystroke, which the 400 ms debounce in `browser` absorbs.
"""

from __future__ import annotations

import bpy
from bpy.props import StringProperty

from . import browser

QUERY = "fab_bridge_query"


def _on_query(self, context) -> None:
    browser.request_search(self.fab_bridge_query)


def register() -> None:
    setattr(
        bpy.types.Scene,
        QUERY,
        StringProperty(
            name="Search",
            description="Search the Fab Megascans catalogue",
            default="",
            options={"TEXTEDIT_UPDATE"},
            update=_on_query,
        ),
    )


def unregister() -> None:
    if hasattr(bpy.types.Scene, QUERY):
        delattr(bpy.types.Scene, QUERY)
