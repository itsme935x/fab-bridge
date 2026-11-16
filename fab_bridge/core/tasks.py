"""Main-thread hand-off for worker results (SPEC.md §4.2).

Workers never touch bpy. They call `post()` with a plain callable; the timer
pump built into `core.platform` drains the queue on the main thread.

Why a queue rather than `bpy.app.timers` from the worker: registering a timer
from a worker thread is exactly the kind of bpy call the threading rules ban.
"""

from __future__ import annotations

import collections
import threading
from typing import Any, Callable, Optional

from . import log, platform

log = log.get("tasks")

_queue: "collections.deque[tuple[Callable[..., Any], tuple, dict]]" = collections.deque()
_lock = threading.Lock()


def post(fn: Callable[..., Any], *args, **kwargs) -> None:
    """Run `fn(*args)` on the next main-thread tick."""
    with _lock:
        _queue.append((fn, args, kwargs))
    platform.mark_dirty()


def run_pending(limit: int = 24) -> int:
    """Main thread only. Returns how many callbacks ran."""
    ran = 0
    while ran < limit:
        with _lock:
            if not _queue:
                break
            fn, args, kwargs = _queue.popleft()
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a bad callback must not stop the pump
            log.exception("deferred callback failed")
        ran += 1
    return ran


def spawn(
    fn: Callable[..., Any],
    *args,
    done: Optional[Callable[[Any, Optional[BaseException]], None]] = None,
    name: str = "fab-worker",
    **kwargs,
) -> threading.Thread:
    """Run `fn` on a daemon thread; deliver `(result, error)` back on the main
    thread through `post()`."""

    def _run() -> None:
        result: Any = None
        error: Optional[BaseException] = None
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised on main thread
            error = exc
        if done is not None:
            post(done, result, error)

    thread = threading.Thread(target=_run, name=name, daemon=True)
    thread.start()
    return thread


def pending() -> int:
    with _lock:
        return len(_queue)


def clear() -> None:
    """unregister(): drop callbacks queued for a pump that no longer exists."""
    with _lock:
        _queue.clear()
