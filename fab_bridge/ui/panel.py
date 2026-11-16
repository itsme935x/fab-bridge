"""N-panel (SPEC.md §12.1): account, search, filters, grid, detail.

Everything here is drawn on the main thread from state that `ui.browser`
maintains; `draw()` never performs network I/O or a preview load.
"""

from __future__ import annotations

import bpy

from ..api import endpoints as ep
from ..api.models import QUALITY_LABELS, resolution_label
from ..auth import manager as auth
from ..core import platform, tasks
from ..download import manager as downloads
from . import browser, ops, prefs, previews

CATEGORY = "Fab"

STAGES = {
    downloads.QUEUED: "Queued",
    downloads.RESOLVING: "Preparing",
    downloads.DOWNLOADING: "Downloading",
    downloads.VERIFYING: "Checking",
    downloads.EXTRACTING: "Extracting",
    downloads.IMPORTING: "Importing",
    downloads.DONE: "Done",
    downloads.FAILED: "Failed",
    downloads.CANCELLED: "Cancelled",
}

TYPE_LABELS = (
    ("material", "Surfaces"),
    ("3d-model", "3D"),
    ("decal", "Decals"),
    ("brush", "Brushes"),
)
TYPE_SHORT = dict(TYPE_LABELS)
GROUP_LABELS = {"rendering-style": "Style", "period-theme": "Theme"}


def _expiry_label(seconds: float) -> str:
    if seconds <= 0:
        return "expired"
    if seconds < 3600:
        return f"{int(seconds // 60)} min left"
    return f"{seconds / 3600:.1f} h left"


def _settings(context):
    try:
        return prefs.get(context)
    except Exception:  # noqa: BLE001 - panel must never fail on a missing pref
        return None


def _blocked() -> bool:
    """Every browser sub-panel redraws on its own, so each one checks this."""
    return not platform.online_access()


class FABBRIDGE_PT_main(bpy.types.Panel):
    bl_idname = "FABBRIDGE_PT_main"
    bl_label = "Fab Bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    def draw(self, context):
        layout = self.layout

        if not platform.online_access():
            box = layout.box()
            box.alert = True
            box.label(text="Online access is disabled", icon="ERROR")
            box.label(text="Blender blocks addon network calls until you allow them.")
            box.label(text="Preferences > System > Network > Allow Online Access")
            box.operator(
                "fab_bridge.open_network_prefs", text="Open Network Preferences", icon="PREFERENCES"
            )
            return

        self._account(layout)

    def _account(self, layout) -> None:
        box = layout.box()

        if ops.login_active():
            # The modal operator owns the browser/clipboard; the panel is the
            # only place that can explain what is happening while it waits.
            if ops.LOGIN.get("mode") == "browser":
                box.label(text="Waiting for the Epic code…", icon="SORTTIME")
                steps = box.column(align=True)
                steps.label(text="1. Sign in on the page that opened.")
                steps.label(text="2. Click the redirect link it shows.")
                steps.label(text="   A certificate warning is expected.")
                steps.label(text="3. Or copy the page instead.")
                row = box.row(align=True)
                row.operator("fab_bridge.cancel_login", text="Cancel", icon="CANCEL")
                row.operator("fab_bridge.login_clipboard", text="Copy only", icon="PASTEDOWN")
            else:
                box.label(text="Waiting for the Epic code…", icon="SORTTIME")
                steps = box.column(align=True)
                steps.label(text="1. Sign in on the page that opened.")
                steps.label(text="2. Press Ctrl+A then Ctrl+C.")
                steps.label(text="3. Blender does the rest.")
                row = box.row(align=True)
                row.operator("fab_bridge.cancel_login", text="Cancel", icon="CANCEL")
                row.operator("fab_bridge.login_paste", text="Paste instead", icon="PASTEDOWN")
            return

        if auth.MANAGER.signed_in:
            bundle = auth.MANAGER.bundle()
            name = bundle.display_name or bundle.account_id or "Signed in"
            split = box.row().split(factor=0.58)
            split.label(text=name, icon="CHECKMARK")
            right = split.row(align=True)
            right.alignment = "RIGHT"
            right.operator("fab_bridge.check_session", text="", icon="FILE_REFRESH")
            right.operator("fab_bridge.sign_out", text="", icon="LOOP_BACK")
            box.label(text=_expiry_label(auth.MANAGER.expires_in()), icon="SORTTIME")
            return

        header = box.row(align=True)
        header.label(text="Not signed in", icon="USER")
        row = box.row(align=True)
        row.scale_y = 1.5
        row.operator("fab_bridge.login", text="Sign in with Epic", icon="URL")
        row = box.row(align=True)
        row.operator("fab_bridge.login_paste", text="Paste a code", icon="PASTEDOWN")
        note = box.column(align=True)
        note.label(text="Epic ends on a JSON page.", icon="INFO")
        note.label(text="Ctrl+A then Ctrl+C copies it.")


class FABBRIDGE_PT_search(bpy.types.Panel):
    bl_idname = "FABBRIDGE_PT_search"
    bl_label = "Search"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_parent_id = "FABBRIDGE_PT_main"
    bl_order = 0

    def draw(self, context):
        if _blocked():
            return
        browser.ensure_started()
        state = browser.STATE
        layout = self.layout
        settings = _settings(context)

        row = layout.row(align=True)
        row.prop(context.scene, "fab_bridge_query", text="")
        row.operator("fab_bridge.search", text="", icon="VIEWZOOM")

        row = layout.row(align=True)
        if settings is not None:
            # An enum does not expand, so it goes first; the label clips instead.
            row.prop(settings, "default_sort", text="")
        if state.loading and not state.items:
            row.label(text="Searching…", icon="SORTTIME")
        elif state.error:
            row.label(text=state.error, icon="ERROR")
        elif state.total:
            row.label(text=f"{state.total:,} assets", icon="IMAGE_DATA")
        elif state.items:
            row.label(text=f"{len(state.items)} loaded", icon="IMAGE_DATA")
        else:
            row.label(text="No results", icon="INFO")

        row = layout.row(align=True)
        if settings is not None:
            # A checkbox, not a toggle button: it filters, it is not the action
            # the user came here to press.
            row.prop(settings, "importable_only", text="Blender-ready")
        if state.query.has_chips():
            row.operator("fab_bridge.clear_filters", text="Clear", icon="X")


class FABBRIDGE_PT_filters(bpy.types.Panel):
    bl_idname = "FABBRIDGE_PT_filters"
    bl_label = "Filters"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_parent_id = "FABBRIDGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 1

    def draw(self, context):
        if _blocked():
            return
        browser.ensure_started()
        browser.ensure_tag_groups()
        state = browser.STATE
        query = state.query
        layout = self.layout

        layout.label(text="Type")
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        for code, label in TYPE_LABELS:
            count = state.facets.get(code, 0)
            cell = grid.column()
            cell.enabled = bool(count) or query.listing_type == code
            op = cell.operator(
                "fab_bridge.set_type",
                text=f"{label} {browser.format_count(count)}",
                depress=query.listing_type == code,
            )
            op.listing_type = code

        if state.tag_groups:
            layout.separator()
            for group in state.tag_groups:
                tags = [tag for tag in group.get("tags") or () if isinstance(tag, dict)]
                if not tags:
                    continue
                layout.label(text=GROUP_LABELS.get(str(group.get("slug")), str(group.get("name") or "")))
                chips = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
                for tag in tags:
                    slug = str(tag.get("slug") or "")
                    if not slug:
                        continue
                    chips.operator(
                        "fab_bridge.toggle_tag",
                        text=str(tag.get("name") or slug),
                        depress=slug in query.tags,
                    ).tag = slug
        elif state.tags_loading:
            layout.label(text="Loading tags…", icon="SORTTIME")


def _columns(context) -> int:
    settings = _settings(context)
    value = getattr(settings, "thumbnail_columns", "auto") if settings else "auto"
    if value in {"1", "2", "3"}:
        return int(value)
    # Auto: the sidebar is ~200 px wide, so two columns would clip every title.
    return 1


def _card(layout, listing) -> None:
    state = browser.STATE
    box = layout.box()
    col = box.column(align=True)
    col.alignment = "CENTER"

    selected = listing.uid == state.selected_uid
    # The selected card shows the full-size still (it is already fetched for
    # the detail); until it arrives the thumbnail keeps the card stable.
    icon_id = previews.hero(listing) if selected else 0
    if not icon_id:
        icon_id = previews.icon(listing.uid, listing.thumb(previews.GRID_WIDTH))
    if icon_id:
        # scale is in icon units (16 px), so 8 ≈ 128 px before DPI scaling.
        col.template_icon(icon_value=icon_id, scale=8.0)
    else:
        col.label(text="", icon="IMAGE_DATA")

    col.label(text=listing.title, icon="LAYER_ACTIVE" if selected else "NONE")

    if not browser.usable(listing):
        col.label(text="Not for Blender", icon="ERROR")
        row = col.row(align=True)
        row.operator("fab_bridge.open_fab", text="See on Fab", icon="URL").uid = listing.uid
        return

    kind = TYPE_SHORT.get(listing.listing_type, listing.listing_type.title())
    col.label(text=f"{kind} · {listing.format_label()}")

    # Every card gets a visible button: a bare thumbnail with a title looked
    # like a picture, not something you could click.
    row = col.row(align=True)
    row.scale_y = 1.15
    details = row.operator(
        "fab_bridge.select_listing",
        text="Selected" if selected else "Formats",
        icon="CHECKMARK" if selected else "INFO",
        depress=selected,
    )
    details.uid = listing.uid
    row.operator("fab_bridge.open_fab", text="", icon="URL").uid = listing.uid


class FABBRIDGE_PT_results(bpy.types.Panel):
    bl_idname = "FABBRIDGE_PT_results"
    bl_label = "Results"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_parent_id = "FABBRIDGE_PT_main"
    bl_order = 2

    def draw(self, context):
        if _blocked():
            return
        browser.ensure_started()
        state = browser.STATE
        layout = self.layout

        if state.error and not state.items:
            layout.label(text=state.error, icon="ERROR")
            return
        if not state.items:
            if state.loading:
                layout.label(text="Searching…", icon="SORTTIME")
            elif state.query.has_chips():
                layout.label(text="Nothing matches those filters.", icon="INFO")
                layout.operator("fab_bridge.clear_filters", text="Clear filters", icon="X")
            else:
                layout.label(text="No assets found — try another word.", icon="INFO")
            return

        # Rows are built by hand instead of `grid_flow` so the selected card's
        # details can be inserted directly below its row, not at the end.
        columns = _columns(context)
        for start in range(0, len(state.items), columns):
            chunk = state.items[start:start + columns]
            if columns == 1:
                _card(layout, chunk[0])
            else:
                row = layout.row(align=True)
                for listing in chunk:
                    _card(row.column(align=True), listing)
            selected = next((item for item in chunk if item.uid == state.selected_uid), None)
            if selected is not None:
                _detail(layout, selected)

        row = layout.row()
        row.scale_y = 1.2
        row.enabled = bool(state.cursor) and not state.loading
        row.operator("fab_bridge.load_more", text="Load more" if state.cursor else "End of results")


def _detail(layout, listing) -> None:
    """The selected asset's actions and files, directly under its card."""
    state = browser.STATE
    box = layout.box()
    if state.detail_loading:
        box.label(text="Loading files…", icon="SORTTIME")
        return
    if state.detail_error:
        box.label(text=state.detail_error, icon="ERROR")
        return

    detail = state.detail
    if detail is None or detail.listing.uid != listing.uid:
        return

    files = detail.files
    has_files = bool(files and files.files)
    signed_in = auth.MANAGER.signed_in
    row = box.row(align=True)
    row.scale_y = 1.4
    row.enabled = has_files and signed_in
    primary = row.operator("fab_bridge.download", text="Download & Import", icon="IMPORT")
    primary.uid = listing.uid
    # Pin the format the panel is showing, so the button cannot silently
    # download a different format than the file list below it.
    primary.format_code = files.format.code if files else ""
    if not has_files:
        box.label(text=detail.note() or "No files listed", icon="INFO")
    elif not signed_in:
        box.label(text="Sign in to download.", icon="USER")
        box.label(text="Free assets need an account too.")

    # Category and seller are context, not controls: one compact line.
    meta = " · ".join(part for part in (listing.category_name, listing.seller) if part)
    if meta:
        box.label(text=meta, icon="FILE_FOLDER")

    selected_code = files.format.code if files else ""
    if listing.formats:
        box.label(text="Formats", icon="FILE")
        # Wrapped two per row: a single column made the panel scroll for
        # six one-word lines. The selected format keeps its dot marker.
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        for fmt in listing.formats:
            cell = grid.column()
            prefix = "• " if fmt.code == selected_code else ""
            cell.label(text=prefix + fmt.name)

    if has_files:
        _file_tiers(box, listing, files)

    box.operator("fab_bridge.open_fab", text="Open on Fab", icon="URL").uid = listing.uid


def _file_tiers(box, listing, files) -> None:
    ordered = files.ordered()
    open_ = browser.STATE.files_open
    # The tier name alone ("High") does not say 4K anywhere, so the header and
    # every row carry the texture resolution.
    span = _resolution_span(ordered)
    row = box.row(align=True)
    row.operator(
        "fab_bridge.toggle_files",
        text=f"Files ({span})" if span else f"Files ({len(ordered)})",
        icon="DISCLOSURE_TRI_DOWN" if open_ else "DISCLOSURE_TRI_RIGHT",
    )
    if not open_:
        return
    for entry in ordered:
        row = box.row(align=True)
        label = QUALITY_LABELS.get(entry.quality, "File")
        resolution = resolution_label(entry.name, entry.quality)
        row.label(text=f"{label} · {resolution}" if resolution else label)
        right = row.row(align=True)
        right.alignment = "RIGHT"
        right.label(text=entry.size_label())
        get = right.operator("fab_bridge.download", text="Get", icon="IMPORT")
        get.uid = listing.uid
        get.quality = entry.quality or ""
        get.format_code = files.format.code


def _resolution_span(entries) -> str:
    labels = [resolution_label(entry.name, entry.quality) for entry in entries]
    labels = [label for label in labels if label]
    if not labels:
        return ""
    if len(set(labels)) == 1:
        return labels[0]
    return f"{labels[0]}–{labels[-1]}"


class FABBRIDGE_PT_queue(bpy.types.Panel):
    bl_idname = "FABBRIDGE_PT_queue"
    bl_label = "Queue"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_parent_id = "FABBRIDGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    def draw(self, context):
        if _blocked():
            return
        layout = self.layout
        jobs = downloads.MANAGER.jobs

        if not jobs:
            layout.label(text="Nothing downloaded yet.", icon="INFO")
        else:
            for job in jobs[-6:]:
                self._job(layout, job)
            if any(not job.active for job in jobs):
                layout.operator("fab_bridge.clear_downloads", text="Clear finished", icon="X")

        _cache(layout)

    def _job(self, layout, job) -> None:
        box = layout.box()
        column = box.column(align=True)

        row = column.row(align=True)
        row.label(text=job.title or job.uid)
        if job.active:
            row.operator("fab_bridge.cancel_download", text="", icon="CANCEL").job_id = job.id

        row = column.row(align=True)
        row.label(text=STAGES.get(job.stage, job.stage.title()))
        if job.stage == downloads.DOWNLOADING and job.size:
            row.label(text=f"{job.fraction * 100:.0f}%")
            if job.speed:
                row.label(text=f"{job.speed / 1_000_000:.1f} MB/s")
        elif job.quality:
            row.label(text=job.quality.title())

        if job.stage == downloads.DOWNLOADING:
            column.label(text=_bar(job.fraction))

        if job.error:
            column.label(text=job.error, icon="ERROR")
        elif job.stage == downloads.DONE and job.summary:
            column.label(text=job.summary, icon="CHECKMARK")
        elif job.note and not job.active:
            column.label(text=job.note, icon="INFO")


def _bar(fraction: float, width: int = 12) -> str:
    filled = max(0, min(width, int(round(fraction * width))))
    return "█" * filled + "░" * (width - filled)


def _size(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f} GB"
    return f"{value / 1_000_000:.0f} MB"


def _cache(layout) -> None:
    # Do not touch (or create) the index just because the sidebar was opened.
    if not downloads.MANAGER.has_index and not downloads.MANAGER.jobs:
        return
    try:
        index = downloads.MANAGER.index()
        count = index.downloaded_count()
        total = index.total_bytes()
    except Exception:  # noqa: BLE001 - an unwritable library must not break the panel
        return
    if count:
        layout.label(text=f"Cache: {_size(total)} ({count} assets)", icon="FILE_FOLDER")


class FABBRIDGE_PT_diagnostics(bpy.types.Panel):
    bl_idname = "FABBRIDGE_PT_diagnostics"
    bl_label = "Diagnostics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_parent_id = "FABBRIDGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 4

    def draw(self, context):
        from .. import VERSION

        state = browser.STATE
        layout = self.layout
        col = layout.column(align=True)
        col.label(text=f"Addon {VERSION} · schema {ep.API_SCHEMA}")
        col.label(text=f"Blender {bpy.app.version_string}")
        col.label(text=ep.FAB_BASE)
        col.label(text=f"Query gen {state.generation} · callbacks {tasks.pending()}")
        col.label(text=f"Online access {'on' if platform.online_access() else 'off'}")
        col.label(text=f"Downloads: {downloads.MANAGER.active_count()} active · "
                       f"{len(downloads.MANAGER.jobs)} shown")
        if state.fetched_at:
            col.label(text=f"Results: {len(state.items)} · cursor {'yes' if state.cursor else 'no'}")


CLASSES = (
    FABBRIDGE_PT_main,
    FABBRIDGE_PT_search,
    FABBRIDGE_PT_filters,
    FABBRIDGE_PT_results,
    FABBRIDGE_PT_queue,
    FABBRIDGE_PT_diagnostics,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
