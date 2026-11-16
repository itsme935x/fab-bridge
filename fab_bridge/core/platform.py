"""Blender-specific helpers kept in one place so nothing else imports bpy
at module scope (that keeps the api/auth layers testable headless).

Thread rule (SPEC.md §4.2): workers never touch bpy. `redraw()` is safe to call
from any thread — off the main thread it only sets a flag that the UI pump
picks up on the next tick.
"""

from __future__ import annotations

import threading

import bpy

PANEL_TAG = "fab_bridge"
PUMP_INTERVAL = 0.1

_dirty = False
_lock = threading.Lock()
_ticks: list = []


def online_access() -> bool:
    """Blender 4.2+ global network switch. Absent in older builds."""
    return bool(getattr(bpy.app, "online_access", True))


def is_background() -> bool:
    return bpy.app.background


def mark_dirty() -> None:
    global _dirty
    with _lock:
        _dirty = True


def redraw() -> None:
    """Repaint panels now (main thread) or soon (worker thread)."""
    if threading.current_thread() is not threading.main_thread():
        mark_dirty()
        return
    _redraw_now()


def _redraw_now() -> None:
    try:
        wm = bpy.context.window_manager
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type in {"VIEW_3D", "PREFERENCES"}:
                    area.tag_redraw()
    except Exception:  # noqa: BLE001 - never let a redraw break a callback
        pass


def add_tick(fn) -> None:
    """Register a main-thread callback that runs on every pump tick."""
    if fn not in _ticks:
        _ticks.append(fn)


def remove_tick(fn) -> None:
    if fn in _ticks:
        _ticks.remove(fn)


def pump() -> float:
    """bpy.app.timers callback: the single place main-thread work lands."""
    global _dirty

    from . import log, tasks  # noqa: PLC0415 - import cycle is deliberate

    tasks.run_pending()

    for fn in list(_ticks):
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.get("pump").exception("tick callback failed")

    with _lock:
        dirty, _dirty = _dirty, False
    if dirty:
        _redraw_now()
    return PUMP_INTERVAL


def notify(message: str, *, error: bool = False) -> None:
    """Non-blocking user feedback. Main thread only."""
    try:
        bpy.context.window_manager.popup_menu(
            lambda self, _ctx: self.layout.label(text=message),
            title="Fab Bridge",
            icon="ERROR" if error else "INFO",
        )
    except Exception:  # noqa: BLE001 - background mode has no window manager
        pass
