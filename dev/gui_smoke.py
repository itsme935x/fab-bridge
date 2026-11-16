"""GUI smoke: draw a real Fab panel in a real Blender window.

    blender --factory-startup --python dev/gui_smoke.py -- FABBRIDGE_PT_results
    blender --factory-startup --python dev/gui_smoke.py -- FABBRIDGE_PT_results --files

Headless runs cannot catch layout errors (only a real `UILayout` can). The
addon is registered from this repo, a live search runs, the first result is
selected, and the requested panel is opened with `wm.call_panel` (a real draw
with a real layout), then the window is screenshotted to
`%TEMP%\\fab_bridge_<panel>.png` and Blender quits.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import bpy  # noqa: E402

import fab_bridge  # noqa: E402
from fab_bridge.core import platform  # noqa: E402

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DEMO_LOGIN = "--login" in ARGV
QUEUE_DEMO = "--queue-demo" in ARGV
OPEN_FILES = "--files" in ARGV
PANEL = next((arg for arg in ARGV if arg.startswith("FABBRIDGE_")), "FABBRIDGE_PT_results")
SUFFIX = "login" if DEMO_LOGIN else ("queue" if QUEUE_DEMO else PANEL.split("_")[-1])
SHOT = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), f"fab_bridge_{SUFFIX}.png")
STAGE_TIMEOUT = 90.0

state = {"stage": "start", "deadline": time.monotonic() + STAGE_TIMEOUT}


def log(*parts):
    print("GUI_SMOKE", PANEL, *parts, flush=True)


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    return win, area


def finish(area, window, message=""):
    if message:
        log(message)
    try:
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.screen.screenshot(filepath=SHOT)
        log("screenshot", SHOT)
    except Exception:  # noqa: BLE001
        log("SCREENSHOT_TRACEBACK", traceback.format_exc())
    bpy.ops.wm.quit_blender()
    return None


def dismiss_splash():
    """Send Esc to the foreground Blender window to close the startup splash.

    `bpy.ops.wm.splash()` is not a valid operator in 5.3, and the splash is not
    exposed as a window or screen, so the only reliable way to get a clean
    screenshot is a real key event.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.keybd_event(0x1B, 0, 0, 0)  # VK_ESCAPE down
        user32.keybd_event(0x1B, 0, 0x0002, 0)  # KEYEVENTF_KEYUP
    except Exception:  # noqa: BLE001 - not Windows, or no UI
        pass


def tick():
    from fab_bridge.ui import browser, previews

    try:
        if time.monotonic() > state["deadline"]:
            window, area = view3d()
            return finish(area, window, f"TIMEOUT at {state['stage']}")

        window, area = view3d()

        if state["stage"] == "start":
            if DEMO_LOGIN:
                from fab_bridge.ui import ops

                ops.LOGIN.update(active=True, started=time.monotonic(), mode="browser")
                state["stage"] = "draw"
                return 0.5
            if QUEUE_DEMO:
                from types import SimpleNamespace

                from fab_bridge.download import manager as downloads

                downloads.MANAGER.jobs = [
                    downloads.Job(
                        uid="demo1", id="job1", title="Asphalt Road", quality="high",
                        format_code="texture-set", size=112_000_000,
                        done_bytes=79_000_000, speed=8_200_000,
                        stage=downloads.DOWNLOADING,
                    ),
                    downloads.Job(
                        uid="demo2", id="job2", title="Tundra Grass", quality="mid",
                        format_code="fbx", stage=downloads.QUEUED,
                    ),
                    downloads.Job(
                        uid="demo3", id="job3", title="Forest Rock", quality="mid",
                        format_code="converted-files", stage=downloads.DONE,
                        summary="Imported 5 objects",
                    ),
                ]
                downloads.MANAGER.index = lambda root=None: SimpleNamespace(
                    downloaded_count=lambda: 34, total_bytes=lambda: 12_400_000_000
                )
                state["stage"] = "draw"
                return 0.5
            bpy.context.scene.fab_bridge_query = "asphalt"
            log("search", bpy.ops.fab_bridge.search())
            state["stage"] = "searching"
            return 0.5

        if state["stage"] == "searching":
            if browser.STATE.loading:
                return 0.5
            log("results", len(browser.STATE.items), "total", browser.STATE.total,
                "error", browser.STATE.error)
            if not browser.STATE.items:
                return finish(area, window)
            first = browser.STATE.items[0]
            log("select", first.title, bpy.ops.fab_bridge.select_listing(uid=first.uid))
            state["stage"] = "detail"
            return 0.5

        if state["stage"] == "detail":
            if browser.STATE.detail_loading:
                return 0.5
            log("detail", browser.STATE.detail is not None, "err", browser.STATE.detail_error)
            if OPEN_FILES:
                browser.STATE.files_open = True
            state["stage"] = "draw"
            return 0.5

        if state["stage"] == "draw":
            with bpy.context.temp_override(window=window, area=area):
                result = bpy.ops.wm.call_panel(name=PANEL, keep_open=True)
            log("call_panel", result)
            state["stage"] = "settle"
            state["settle"] = time.monotonic() + 4.0
            return 0.5

        if state["stage"] == "settle":
            if time.monotonic() < state["settle"]:
                return 0.5
            log("previews_loaded", len(previews._loaded))
            return finish(area, window)
    except Exception:  # noqa: BLE001
        window, area = view3d()
        log("TRACEBACK", traceback.format_exc())
        return finish(area, window)
    return 0.5


def main():
    try:
        bpy.context.preferences.system.use_online_access = True
        # Never write this throwaway session's prefs over the user's real ones.
        bpy.context.preferences.use_preferences_save = False
        try:
            log("addon_enable", bpy.ops.preferences.addon_enable(module="fab_bridge"))
        except Exception:  # noqa: BLE001 - already registered, or no addon path
            log("TRACEBACK", traceback.format_exc())
            fab_bridge.register()
        log("registered", bpy.app.version_string, "online", platform.online_access())
        bpy.context.preferences.view.show_splash = False
        dismiss_splash()
        bpy.app.timers.register(tick, first_interval=1.0)
    except Exception:  # noqa: BLE001
        log("TRACEBACK", traceback.format_exc())
        bpy.ops.wm.quit_blender()


main()
