"""Real-Blender smoke test for Fab Bridge (M1 + M2).

    blender --factory-startup --background --python dev/blender_smoke.py

Imports the addon from this repo (no install) and exercises the live pipeline
with the real `bpy`: registration, a real search, pagination, detail, thumbnail
download + preview loading, then teardown. Prints one JSON blob at the end.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import bpy  # noqa: E402

import fab_bridge  # noqa: E402
from fab_bridge.core import platform  # noqa: E402

# Never leave a headless Blender hanging if a network call wedges.
_watchdog = threading.Timer(240.0, lambda: os._exit(2))
_watchdog.daemon = True
_watchdog.start()

report: dict = {"blender": bpy.app.version_string, "checks": {}}


def wait_for(predicate, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        platform.pump()
        if predicate():
            return True
        time.sleep(0.03)
    return False


def check(name: str, ok: bool, value=None) -> None:
    report["checks"][name] = {"ok": bool(ok), "value": value}


try:
    fab_bridge.register()
    check("registered", hasattr(bpy.types, "FABBRIDGE_PT_main"), dir(bpy.types).count("FABBRIDGE_PT_main"))
    check("pump_timer", bpy.app.timers.is_registered(platform.pump))
    check("scene_prop", hasattr(bpy.types.Scene, "fab_bridge_query"))
    check("network_prefs_op", hasattr(bpy.ops.fab_bridge, "open_network_prefs"))

    # `--factory-startup` leaves "Allow Online Access" off (Blender's default for
    # a fresh install). Exercise the consent gate in both directions, then turn
    # it on the way a normal user's preferences have it.
    def run_op(name, **kwargs):
        """bpy.ops from a script raises RuntimeError when the operator reports ERROR."""
        try:
            return getattr(bpy.ops.fab_bridge, name)(**kwargs)
        except (RuntimeError, TypeError) as exc:
            return {"RAISED": str(exc)}

    prefs = bpy.context.preferences.system
    report["online_access_default"] = platform.online_access()
    try:
        prefs.use_online_access = False
        gated = run_op("search")
        check("gate_blocks_when_off", "CANCELLED" in gated or "RAISED" in gated, gated)
        prefs.use_online_access = True
        check("online_access_on", platform.online_access())
    except Exception:
        report["gate_traceback"] = traceback.format_exc()

    from fab_bridge.ui import browser, previews

    bpy.context.scene.fab_bridge_query = "asphalt"
    started = run_op("search")
    check("search_operator", started == {"FINISHED"}, started)
    ok = wait_for(lambda: not browser.STATE.loading, 60.0)
    state = browser.STATE
    check("search_completed", ok)
    check("search_items", len(state.items) >= 20, len(state.items))
    check("search_total", state.total > 0, state.total)
    check("search_error", state.error == "", state.error)
    report["first_titles"] = [item.title for item in state.items[:3]]
    report["facets"] = dict(state.facets)

    # Pagination through the opaque cursor.
    if state.cursor:
        before = len(state.items)
        browser.load_more()
        ok = wait_for(lambda: not browser.STATE.loading, 60.0)
        check("load_more", ok and len(browser.STATE.items) > before, len(browser.STATE.items))

    # Detail + format file sizes.
    if state.items:
        target = state.items[0]
        browser.select(target.uid)
        wait_for(lambda: not browser.STATE.detail_loading, 45.0)
        detail = browser.STATE.detail
        check("detail_loaded", detail is not None, browser.STATE.detail_error)
        if detail:
            report["detail"] = {
                "title": detail.listing.title,
                "formats": list(detail.listing.format_codes),
                "chosen": detail.files.format.code if detail.files else None,
                "files": [
                    {"quality": entry.quality, "size": entry.size_label()}
                    for entry in detail.files.ordered()
                ] if detail.files else [],
            }
            check("detail_files", bool(detail.files and detail.files.files))

            thumb = target.thumb(previews.GRID_WIDTH)
            previews.icon(target.uid, thumb)
            loaded = wait_for(lambda: len(previews._loaded) >= 1, 60)
            check("thumbnail_loaded", loaded, len(previews._loaded))
            if thumb:
                width, url = thumb
                path = previews.path_for(target.uid, url, width)
                check("thumbnail_on_disk", __import__("os").path.exists(path), path)

            previews.hero(detail.listing)
            check("hero_loaded", wait_for(lambda: len(previews._loaded) >= 2, 60), len(previews._loaded))
            check("preview_failures", not previews._failed, list(previews._failed))
except Exception:
    report["traceback"] = traceback.format_exc()
finally:
    try:
        fab_bridge.unregister()
        check("unregistered", not hasattr(bpy.types, "FABBRIDGE_PT_main"))
        check("timer_removed", not bpy.app.timers.is_registered(platform.pump))
        check("scene_prop_removed", not hasattr(bpy.types.Scene, "fab_bridge_query"))
    except Exception:
        report["teardown_traceback"] = traceback.format_exc()

failures = [name for name, result in report["checks"].items() if not result["ok"]]
report["failures"] = failures
_watchdog.cancel()
print("FABBRIDGE_SMOKE_BEGIN")
print(json.dumps(report, indent=2, default=str))
print("FABBRIDGE_SMOKE_END")

if failures or "traceback" in report or "teardown_traceback" in report:
    raise SystemExit(1)
