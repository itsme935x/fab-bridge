"""Thumbnail and hero-image cache (SPEC.md §12.5).

Rule: `bpy.utils.previews` can only load from a real file path, so the flow is
always `worker downloads to cache → main-thread tick → pcoll.load`.
`draw()` never touches the network or the filesystem.
"""

from __future__ import annotations

import hashlib
import os
import re

import bpy

from ..api import transport
from ..core import log, tasks

log = log.get("previews")

GRID_WIDTH = 320
HERO_LIMIT = 1920
PREFETCH = 12
MAX_ENTRIES = 256

_UID = re.compile(r"^[0-9a-fA-F-]{8,64}$")

_pcoll = None
_previews = None
_loaded: dict[str, int] = {}
_pending: set[str] = set()
_failed: set[str] = set()


# ---------------------------------------------------------------------------
# cache paths
# ---------------------------------------------------------------------------
def _cache_root() -> str:
    try:
        base = bpy.utils.user_resource("CACHE")
    except Exception:  # pragma: no cover - background mode fallback
        base = os.path.join(os.path.expanduser("~"), ".cache")
    path = os.path.join(base, "fab_bridge", "thumbs")
    os.makedirs(path, exist_ok=True)
    return path


def _folder(uid: str) -> str:
    name = uid if _UID.match(uid) else hashlib.sha1(uid.encode()).hexdigest()[:16]
    path = os.path.join(_cache_root(), name)
    os.makedirs(path, exist_ok=True)
    return path


def _token(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]


def path_for(uid: str, url: str, width: int) -> str:
    """Immutable filename: a cached file is valid forever."""
    return os.path.join(_folder(uid), f"{int(width)}-{_token(url)}.jpg")


# ---------------------------------------------------------------------------
# preview collection
# ---------------------------------------------------------------------------
def _module():
    """`bpy.utils.previews` is a submodule: `import bpy` does not expose it (5.x)."""
    global _previews
    if _previews is None:
        from bpy.utils import previews

        _previews = previews
    return _previews


def collection():
    global _pcoll
    if _pcoll is None:
        _pcoll = _module().new()
    return _pcoll


def _evict() -> None:
    if len(_loaded) <= MAX_ENTRIES:
        return
    pcoll = collection()
    for key in list(_loaded)[: len(_loaded) - MAX_ENTRIES]:
        _loaded.pop(key, None)
        try:
            pcoll.remove(key)
        except (KeyError, RuntimeError):
            pass


def _load(key: str, path: str) -> None:
    """Main thread. Safe to call twice; both paths are idempotent."""
    _pending.discard(key)
    if key in _loaded:
        return
    try:
        pcoll = collection()
        preview = pcoll.load(key, path, "IMAGE")
    except Exception as exc:  # noqa: BLE001 - a bad image must not kill the pump
        _failed.add(key)
        log.debug("preview load failed for %s: %s", key, type(exc).__name__)
        return
    _loaded[key] = preview.icon_id
    _evict()


def _finish(key: str, path, error) -> None:
    if error is not None or not path:
        _pending.discard(key)
        _failed.add(key)
        log.debug("preview download failed: %s", type(error).__name__ if error else "?")
        return
    _load(key, str(path))


# ---------------------------------------------------------------------------
# public API (main thread only)
# ---------------------------------------------------------------------------
def icon(uid: str, image: tuple[int, str] | None) -> int:
    """Icon id for `(width, url)`, or 0 while it loads. Never blocks."""
    if not uid or not image:
        return 0
    width, url = image
    key = f"{uid}:{int(width)}:{_token(url)}"

    cached = _loaded.get(key)
    if cached:
        return cached
    if key in _pending or key in _failed:
        return 0

    _pending.add(key)
    path = path_for(uid, url, width)
    if os.path.exists(path):
        tasks.post(_load, key, path)  # never load inside draw()
    else:
        tasks.spawn(
            transport.download_to_file,
            url,
            path,
            done=lambda result, error, _key=key: _finish(_key, result, error),
            name="fab-thumb",
        )
    return 0


def hero(listing) -> int:
    image = listing.hero(hero_width())
    return icon(listing.uid, image)


def hero_width() -> int:
    """`prefs.hero_width`, clamped to the range Fab actually serves."""
    try:
        from . import prefs

        settings = prefs.get()
        width = int(getattr(settings, "hero_width", HERO_LIMIT)) if settings else HERO_LIMIT
    except Exception:  # noqa: BLE001 - headless or unregistered
        width = HERO_LIMIT
    return max(320, min(2560, width))


def prefetch(listings) -> None:
    """First screenful only; the rest load when asked for (SPEC §12.5)."""
    for listing in list(listings)[:PREFETCH]:
        icon(listing.uid, listing.thumb(GRID_WIDTH))


def clear() -> None:
    """Main thread, from unregister(): a leaked pcoll makes Blender log errors."""
    global _pcoll
    _loaded.clear()
    _pending.clear()
    _failed.clear()
    if _pcoll is not None:
        try:
            _module().remove(_pcoll)
        except Exception:  # noqa: BLE001
            pass
        _pcoll = None
