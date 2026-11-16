"""Search state, pagination and selection (SPEC.md §4.2 rule 5, §12.2).

Main thread only. Workers fetch; results arrive through `core.tasks.post` and
are dropped if the generation moved on while they were in flight.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..api import endpoints as ep, fab
from ..api.models import FormatFiles, Listing, SearchPage
from ..api.query import Query
from ..core import errors, log, platform, tasks
from . import previews

log = log.get("browser")

DEBOUNCE = 0.4


@dataclass
class Detail:
    listing: Listing
    files: Optional[FormatFiles] = None

    def note(self) -> str:
        if self.files is None:
            return "This listing has no Blender-importable files."
        if not self.files.files:
            return "Fab reports no downloadable files for this format."
        return ""


@dataclass
class Browser:
    query: Query = field(default_factory=Query)
    generation: int = 0
    items: list[Listing] = field(default_factory=list)
    cursor: Optional[str] = None
    facets: dict[str, int] = field(default_factory=dict)
    total: int = 0
    loading: bool = False
    error: str = ""

    selected_uid: str = ""
    detail: Optional[Detail] = None
    detail_loading: bool = False
    detail_error: str = ""
    #: Inline "Files" fold under the selected card; reset on every selection.
    files_open: bool = False

    tag_groups: list[dict] = field(default_factory=list)
    tags_loading: bool = False

    debounce_at: float = 0.0
    started: bool = False
    fetched_at: float = 0.0


STATE = Browser()


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
def ensure_started(*, search: bool = True) -> None:
    """Called from the panel: first draw kicks the initial listing."""
    if STATE.started:
        return
    _apply_prefs()
    STATE.started = True
    platform.add_tick(tick)
    if search:
        run_search()


def _apply_prefs() -> None:
    try:
        from . import prefs  # noqa: PLC0415 - ui.prefs imports bpy only

        settings = prefs.get()
    except Exception:  # noqa: BLE001 - prefs are optional in tests
        return
    if settings is None:
        return
    STATE.query = STATE.query.replaced(
        sort=settings.default_sort,
        importable=bool(settings.importable_only),
    )


def reset() -> None:
    """unregister(): drop state so a reload never reuses a stale generation."""
    platform.remove_tick(tick)
    STATE.__init__()  # dataclass defaults give fresh containers


def tick() -> None:
    if STATE.debounce_at and time.monotonic() >= STATE.debounce_at:
        STATE.debounce_at = 0.0
        run_search()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def request_search(text: Optional[str] = None, *, delay: float = DEBOUNCE) -> None:
    """Debounced entry point for the search box."""
    ensure_started(search=False)
    if text is not None and text != STATE.query.q:
        STATE.query = STATE.query.replaced(q=text)
    STATE.debounce_at = time.monotonic() + delay


def set_query(query: Query) -> None:
    ensure_started(search=False)
    STATE.query = query
    STATE.debounce_at = 0.0
    run_search()


def run_search(*, keep_detail: bool = False) -> None:
    STATE.generation += 1
    generation = STATE.generation
    STATE.loading = True
    STATE.error = ""
    STATE.items = []
    STATE.cursor = None
    STATE.debounce_at = 0.0
    if not keep_detail:
        STATE.selected_uid = ""
        STATE.detail = None
        STATE.detail_error = ""

    query = STATE.query
    tasks.spawn(
        _fetch_search,
        query,
        done=lambda result, error, gen=generation: _search_done(gen, result, error),
        name="fab-search",
    )
    platform.mark_dirty()


def _fetch_search(query: Query):
    page = fab.search(query)
    counts: dict[str, int] = {}
    try:
        counts = fab.facets(query)
    except errors.FabError as exc:
        # Counts are a nicety; a failed facet call must not blank the grid.
        log.debug("facet call failed: %s", type(exc).__name__)
    return page, counts


def _search_done(generation: int, result, error) -> None:
    if generation != STATE.generation:
        return  # stale response from a superseded query
    STATE.loading = False
    if error is not None:
        STATE.error = message(error)
        log.warning("search failed: %s", type(error).__name__)
        platform.redraw()
        return

    page: SearchPage
    counts: dict[str, int]
    page, counts = result
    STATE.items = list(page.listings)
    STATE.cursor = page.cursor
    STATE.facets = counts
    STATE.total = sum(counts.values())
    STATE.fetched_at = time.time()
    previews.prefetch(STATE.items)
    platform.redraw()


def load_more() -> None:
    if STATE.loading or not STATE.cursor:
        return
    generation = STATE.generation
    cursor = STATE.cursor
    query = STATE.query
    STATE.loading = True
    STATE.cursor = None  # prevent a double-click firing two pages
    tasks.spawn(
        fab.search,
        query,
        cursor=cursor,
        done=lambda result, error: _more_done(generation, result, error),
        name="fab-more",
    )
    platform.mark_dirty()


def _more_done(generation: int, result, error) -> None:
    if generation != STATE.generation:
        return
    STATE.loading = False
    if error is not None:
        STATE.error = message(error)
        platform.redraw()
        return
    page: SearchPage = result
    known = {item.uid for item in STATE.items}
    STATE.items.extend(item for item in page.listings if item.uid not in known)
    STATE.cursor = page.cursor
    platform.redraw()


# ---------------------------------------------------------------------------
# selection + detail
# ---------------------------------------------------------------------------
def select(uid: str) -> None:
    if not uid:
        return
    STATE.selected_uid = uid
    STATE.detail = None
    STATE.detail_error = ""
    STATE.detail_loading = True
    STATE.files_open = False
    # Read the preference here, on the main thread, so the worker never touches
    # bpy - and so the detail panel shows the same format a download would pick.
    wanted = wanted_format()
    tasks.spawn(
        _fetch_detail,
        uid,
        wanted,
        done=lambda result, error, target=uid: _detail_done(target, result, error),
        name="fab-detail",
    )
    platform.mark_dirty()


def _fetch_detail(uid: str, wanted: str | None = None) -> Detail:
    listing = fab.get_listing(uid)
    code = preferred_format(listing, wanted)
    files = None
    if code:
        try:
            files = fab.get_format_files(uid, code)
        except errors.NotFound:
            files = None
        except errors.UnsupportedFormat:
            files = None
    return Detail(listing=listing, files=files)


def _detail_done(uid: str, result, error) -> None:
    if uid != STATE.selected_uid:
        return
    STATE.detail_loading = False
    if error is not None:
        STATE.detail_error = message(error)
        log.warning("detail failed: %s", type(error).__name__)
        platform.redraw()
        return
    STATE.detail = result
    previews.hero(result.listing)
    platform.redraw()


def preferred_format(listing: Listing, wanted: str | None = None) -> str:
    """The format a download would pick, per `resolver` (§7.1). Empty if none."""
    from ..download import resolver  # noqa: PLC0415 - keeps the UI layer light

    try:
        return resolver.pick_format(listing, wanted)
    except errors.UnsupportedFormat:
        return ""


def wanted_format() -> str | None:
    """`prefs.prefer_format` as a format code. Main thread only (reads bpy)."""
    from ..download import manager  # noqa: PLC0415

    return manager.wanted_format()


def usable(listing: Listing) -> bool:
    codes = set(listing.format_codes)
    return any(code in codes for code in ep.IMPORTABLE_FORMATS)


# ---------------------------------------------------------------------------
# tag groups (filter chips)
# ---------------------------------------------------------------------------
def ensure_tag_groups() -> None:
    if STATE.tag_groups or STATE.tags_loading:
        return
    STATE.tags_loading = True
    tasks.spawn(_fetch_tags, done=_tags_done, name="fab-tags")
    platform.mark_dirty()


def _fetch_tags():
    groups = fab.tag_groups()
    # Unreal-specific tags are noise in a Blender sidebar.
    return [
        group
        for group in groups
        if str(group.get("slug") or "") not in {"unreal-engine"}
        and str(group.get("name") or "") != "Unreal Engine"
    ]


def _tags_done(result, error) -> None:
    STATE.tags_loading = False
    if error is not None:
        log.debug("tag groups failed: %s", type(error).__name__)
        platform.redraw()
        return
    STATE.tag_groups = list(result or ())
    platform.redraw()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def message(error: BaseException) -> str:
    if isinstance(error, errors.FabError):
        return error.user_message
    return "Something went wrong. See the log for details."


def format_count(value: int) -> str:
    if value >= 1_000:
        return f"{value / 1000:.1f}k"
    return str(value)
