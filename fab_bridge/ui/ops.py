"""Operators (SPEC.md §12.3). M1: sign in, paste a code, sign out, re-check.

The modal login polls the clipboard on a 0.5 s timer and exchanges the code on
a worker thread, so the UI never blocks on the token request.
"""

from __future__ import annotations

import threading
import time
import webbrowser

import bpy
from bpy.props import StringProperty

from ..auth import loopback, manager as auth, oauth
from ..core import errors, log, platform
from ..download import manager as downloads
from . import browser, prefs

log = log.get("ops")

POLL_INTERVAL = 0.5
LOGIN_TIMEOUT = 600.0

#: Read by `ui.panel` so the sidebar can explain the browser step while the
#: modal operator waits.
#:   mode = "browser"   → a local listener is also watching for the redirect
#:   mode = "clipboard" → the user copies the code from Epic's JSON page
LOGIN = {"active": False, "started": 0.0, "mode": "clipboard"}
_cancel_requested = False
#: Set by `fab_bridge.login_clipboard`: the running modal swaps to the JSON page.
_switch_to_clipboard = False
#: Set once the automatic listener has failed, so a retry does not try again.
_prefer_clipboard = False
#: Codes that Epic already refused, so re-opening the modal while the same code
#: is still on the clipboard cannot loop on the same failure.
_rejected_codes: set[str] = set()
MAX_REJECTED = 20


def login_active() -> bool:
    """A modal that died without `_cleanup()` must not wedge the panel."""
    if not LOGIN["active"]:
        return False
    if time.monotonic() - LOGIN["started"] > LOGIN_TIMEOUT + 5:
        LOGIN["active"] = False
        return False
    return True


class _Exchange:
    """Run one token exchange off the main thread and expose the result."""

    def __init__(self, code: str):
        self.code = code
        self.bundle = None
        self.error: Exception | None = None
        self._thread = threading.Thread(target=self._run, name="fab-login", daemon=True)

    def start(self) -> "_Exchange":
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            self.bundle = auth.MANAGER.sign_in_with_code(self.code)
        except Exception as exc:  # noqa: BLE001 - reported in the modal
            self.error = exc

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()


class FABBRIDGE_OT_login(bpy.types.Operator):
    """Sign in to Fab with an Epic Games account"""

    bl_idname = "fab_bridge.login"
    bl_label = "Sign in to Fab"
    bl_description = "Opens the Epic login page and watches the clipboard for the code"

    def invoke(self, context, event):
        global _cancel_requested, _switch_to_clipboard

        if not platform.online_access():
            self.report({"ERROR"}, errors.OnlineAccessDisabled.user_message)
            return {"CANCELLED"}
        if login_active():
            self.report({"INFO"}, "Sign-in is already waiting for a code.")
            return {"CANCELLED"}
        if auth.MANAGER.signed_in:
            self.report({"INFO"}, auth.MANAGER.status())
            return {"FINISHED"}

        _cancel_requested = False
        _switch_to_clipboard = False
        self._exchange: _Exchange | None = None
        self._listener = None

        mode = "clipboard"
        try:
            settings = prefs.get(context)
        except Exception:  # noqa: BLE001 - the modal must not die on a missing pref
            settings = None
        if getattr(settings, "auto_capture", True) and not _prefer_clipboard:
            listener = loopback.Listener()
            if listener.start():
                self._listener = listener
                mode = "browser"

        LOGIN.update(active=True, started=time.monotonic(), mode=mode)
        self._deadline = time.monotonic() + LOGIN_TIMEOUT
        self._started = False
        self._timer = context.window_manager.event_timer_add(POLL_INTERVAL, window=context.window)
        context.window_manager.modal_handler_add(self)
        platform.redraw()
        if mode == "browser":
            self.report({"INFO"}, "Sign in your browser — Blender catches the redirect or reads a copied code.")
        else:
            self.report({"INFO"}, "Sign in, then copy the page Epic shows — it is read from the clipboard.")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "TIMER":
            return self._on_timer(context)
        if event.type in {"ESC", "RIGHTMOUSE"} or _cancel_requested:
            self._cleanup(context)
            self.report({"WARNING"}, "Sign-in cancelled")
            return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def _on_timer(self, context):
        global _switch_to_clipboard

        if _switch_to_clipboard:
            # The browser would not talk to the local listener (or the user
            # prefers copying). Swap to Epic's JSON page without a second modal.
            _switch_to_clipboard = False
            if self._listener is not None:
                self._listener.stop()
                self._listener = None
            LOGIN["mode"] = "clipboard"
            self._started = False
            self.report({"INFO"}, "Copy the code from Epic's page — Blender reads the clipboard.")
            platform.redraw()
            return {"RUNNING_MODAL"}

        if not self._started:
            self._started = True
            # Always the code page. Epic's account portal drops an arbitrary
            # localhost redirect and lands signed-in users on account settings,
            # so a direct redirect to the listener cannot be relied on. The
            # listener stays opportunistic: the JSON page shows a
            # `redirectUrl` link, and clicking it still reaches Blender.
            url = oauth.build_login_url()
            try:
                webbrowser.open(url, new=2)
            except Exception:  # noqa: BLE001
                self.report({"WARNING"}, "Could not open a browser — copy the URL from the log")
                log.info("login url: %s", url)

        if self._exchange is not None:
            if not self._exchange.done:
                return {"RUNNING_MODAL"}
            return self._finish(context)

        if time.monotonic() > self._deadline:
            self._cleanup(context)
            self.report({"WARNING"}, "Timed out waiting for the code")
            return {"CANCELLED"}

        code = ""
        if self._listener is not None:
            code = self._listener.code
        if not code:
            # Copying the page works in either mode; the listener is not a
            # precondition for finishing.
            try:
                clip = context.window_manager.clipboard or ""
            except AttributeError:
                clip = ""
            code = oauth.extract_code(clip)
        if code in _rejected_codes:
            code = ""

        if code:
            log.info("authorization code received via %s", LOGIN["mode"])
            self._exchange = _Exchange(code).start()
            platform.redraw()
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        exchange = self._exchange
        self._cleanup(context)
        if exchange and exchange.bundle:
            label = exchange.bundle.display_name or "your account"
            self.report({"INFO"}, f"Signed in as {label}")
            return {"FINISHED"}
        if exchange is not None and exchange.error is not None:
            # A used/expired code stays on the clipboard; remember it so the
            # next attempt does not fail on the same value.
            _rejected_codes.add(exchange.code)
            if len(_rejected_codes) > MAX_REJECTED:
                _rejected_codes.pop()
            detail = getattr(exchange.error, "detail", "") or str(exchange.error)
            log.warning("login failed: %s (%s)", type(exchange.error).__name__, detail[:200])
        else:
            log.warning("login failed: no bundle and no error")
        message = str(exchange.error) if exchange and exchange.error else "Sign-in failed"
        self.report({"ERROR"}, message)
        return {"CANCELLED"}

    def _cleanup(self, context) -> None:
        LOGIN["active"] = False
        if getattr(self, "_listener", None) is not None:
            self._listener.stop()
            self._listener = None
        if getattr(self, "_timer", None):
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        platform.redraw()


class FABBRIDGE_OT_cancel_login(bpy.types.Operator):
    """Stop waiting for the Epic sign-in code"""

    bl_idname = "fab_bridge.cancel_login"
    bl_label = "Cancel Sign-in"

    def execute(self, context):
        global _cancel_requested

        _cancel_requested = True
        platform.redraw()
        return {"FINISHED"}


class FABBRIDGE_OT_login_clipboard(bpy.types.Operator):
    """Stop waiting for the localhost redirect and read a copied code instead"""

    bl_idname = "fab_bridge.login_clipboard"
    bl_label = "Copy Only"
    bl_description = "Stop listening for the localhost redirect; Blender reads the code from the clipboard"

    def execute(self, context):
        global _switch_to_clipboard, _prefer_clipboard

        _switch_to_clipboard = True
        _prefer_clipboard = True
        platform.redraw()
        return {"FINISHED"}


class FABBRIDGE_OT_login_paste(bpy.types.Operator):
    """Sign in by pasting the Epic authorization code"""

    bl_idname = "fab_bridge.login_paste"
    bl_label = "Sign in with a code"
    bl_description = "Paste the code shown by the Epic sign-in page"

    code: StringProperty(name="Code", description="Authorization code from Epic")

    def invoke(self, context, event):
        if not platform.online_access():
            self.report({"ERROR"}, errors.OnlineAccessDisabled.user_message)
            return {"CANCELLED"}
        # If the code is already on the clipboard, prefill it: one click instead
        # of one click plus a paste. Typed JSON is still accepted either way.
        try:
            clip = context.window_manager.clipboard or ""
        except AttributeError:
            clip = ""
        found = oauth.extract_code(clip)
        if found:
            self.code = found
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        self.layout.label(text="Paste the authorizationCode from Epic's page:")
        self.layout.label(text="(copying the whole JSON blob also works)")
        self.layout.prop(self, "code", text="")

    def execute(self, context):
        code = oauth.extract_code(self.code)
        if not code:
            self.report({"ERROR"}, "That does not look like an Epic authorization code")
            return {"CANCELLED"}
        try:
            bundle = auth.MANAGER.sign_in_with_code(code)
        except errors.FabError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Signed in as {bundle.display_name or 'your account'}")
        return {"FINISHED"}


class FABBRIDGE_OT_sign_out(bpy.types.Operator):
    """Sign out and forget the stored Fab session"""

    bl_idname = "fab_bridge.sign_out"
    bl_label = "Sign Out"
    bl_description = "Clear the stored Epic session"

    def execute(self, context):
        auth.MANAGER.sign_out(remote=True)
        self.report({"INFO"}, "Signed out")
        return {"FINISHED"}


class FABBRIDGE_OT_check_session(bpy.types.Operator):
    """Confirm the session with Fab"""

    bl_idname = "fab_bridge.check_session"
    bl_label = "Check Session"
    bl_description = "Ask Fab whether this session is still valid"

    def execute(self, context):
        from ..api import fab

        try:
            user = fab.get_me()
        except errors.FabError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Signed in as {user.display_name or user.uid}")
        return {"FINISHED"}


class FABBRIDGE_OT_open_network_prefs(bpy.types.Operator):
    """Open the Blender preference that allows online access"""

    bl_idname = "fab_bridge.open_network_prefs"
    bl_label = "Open Network Preferences"
    bl_description = "Open Preferences > System, where 'Allow Online Access' lives"

    def execute(self, context):
        try:
            bpy.ops.screen.userpref_show("INVOKE_DEFAULT", section="SYSTEM")
        except Exception:  # noqa: BLE001 - no window, or an older `section` signature
            log.debug("userpref_show(section='SYSTEM') unavailable")
            try:
                bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                self.report({"ERROR"}, "Open Preferences > System > Network manually")
                return {"CANCELLED"}
        return {"FINISHED"}


class FABBRIDGE_OT_open_fab(bpy.types.Operator):
    """Open this asset on fab.com"""

    bl_idname = "fab_bridge.open_fab"
    bl_label = "Open on Fab"
    bl_description = "Open the listing in your browser"

    uid: StringProperty()

    def execute(self, context):
        if not self.uid:
            return {"CANCELLED"}
        webbrowser.open(f"https://www.fab.com/listings/{self.uid}", new=2)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# browse (M2)
# ---------------------------------------------------------------------------
class FABBRIDGE_OT_search(bpy.types.Operator):
    """Search the Fab Megascans catalogue"""

    bl_idname = "fab_bridge.search"
    bl_label = "Search"
    bl_description = "Run the current query now"

    def execute(self, context):
        if not platform.online_access():
            self.report({"ERROR"}, errors.OnlineAccessDisabled.user_message)
            return {"CANCELLED"}
        text = getattr(context.scene, "fab_bridge_query", "")
        browser.set_query(browser.STATE.query.replaced(q=text))
        return {"FINISHED"}


class FABBRIDGE_OT_load_more(bpy.types.Operator):
    """Load the next page of results"""

    bl_idname = "fab_bridge.load_more"
    bl_label = "Load More"

    def execute(self, context):
        browser.load_more()
        return {"FINISHED"}


class FABBRIDGE_OT_select_listing(bpy.types.Operator):
    """Show this listing's formats and file sizes"""

    bl_idname = "fab_bridge.select_listing"
    bl_label = "Select Asset"
    bl_description = "Fetch this listing's detail and formats"

    uid: StringProperty()

    def execute(self, context):
        browser.select(self.uid)
        return {"FINISHED"}


class FABBRIDGE_OT_set_type(bpy.types.Operator):
    """Filter by asset type (click again to clear)"""

    bl_idname = "fab_bridge.set_type"
    bl_label = "Asset Type"

    listing_type: StringProperty()

    def execute(self, context):
        current = browser.STATE.query.listing_type
        chosen = "" if current == self.listing_type else self.listing_type
        browser.set_query(browser.STATE.query.replaced(listing_type=chosen))
        return {"FINISHED"}


class FABBRIDGE_OT_toggle_tag(bpy.types.Operator):
    """Toggle a tag filter"""

    bl_idname = "fab_bridge.toggle_tag"
    bl_label = "Toggle Tag"

    tag: StringProperty()

    def execute(self, context):
        browser.set_query(browser.STATE.query.toggle_tag(self.tag))
        return {"FINISHED"}


class FABBRIDGE_OT_clear_filters(bpy.types.Operator):
    """Clear type and tag filters"""

    bl_idname = "fab_bridge.clear_filters"
    bl_label = "Clear Filters"
    bl_description = "Keep the search text, drop every chip"

    def execute(self, context):
        browser.set_query(browser.STATE.query.clear_filters())
        return {"FINISHED"}


class FABBRIDGE_OT_download(bpy.types.Operator):
    """Download this asset and import it into Blender"""

    bl_idname = "fab_bridge.download"
    bl_label = "Download & Import"
    bl_description = "Download this asset, extract it and import it into the scene"

    uid: StringProperty()
    quality: StringProperty(description="low, mid, high or raw; empty uses the preference")
    format_code: StringProperty(description="Pin a format instead of the automatic chain")

    def execute(self, context):
        if not platform.online_access():
            self.report({"ERROR"}, errors.OnlineAccessDisabled.user_message)
            return {"CANCELLED"}
        if not self.uid:
            self.report({"ERROR"}, "No asset selected")
            return {"CANCELLED"}
        if not auth.MANAGER.signed_in:
            self.report({"ERROR"}, errors.AuthRequired.user_message)
            return {"CANCELLED"}

        for job in downloads.MANAGER.jobs:
            if not job.active or job.uid != self.uid:
                continue
            if not self.quality or not job.quality or job.quality == self.quality:
                self.report({"INFO"}, f"{job.title or 'That asset'} is already in the queue.")
                return {"CANCELLED"}

        listing = _listing_for(self.uid)
        try:
            job = downloads.MANAGER.enqueue(
                listing=listing,
                uid=self.uid,
                title=listing.title if listing is not None else "",
                quality=self.quality,
                format_code=self.format_code,
            )
        except errors.FabError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Downloading {job.title}…")
        return {"FINISHED"}


class FABBRIDGE_OT_cancel_download(bpy.types.Operator):
    """Cancel a queued or running download"""

    bl_idname = "fab_bridge.cancel_download"
    bl_label = "Cancel Download"
    bl_description = "Stop this download; the partial file is kept so it can resume"

    job_id: StringProperty()

    def execute(self, context):
        if downloads.MANAGER.cancel(self.job_id):
            self.report({"INFO"}, "Cancelling…")
        return {"FINISHED"}


class FABBRIDGE_OT_clear_downloads(bpy.types.Operator):
    """Remove finished downloads from the queue list"""

    bl_idname = "fab_bridge.clear_downloads"
    bl_label = "Clear Finished"

    def execute(self, context):
        downloads.MANAGER.clear_finished()
        return {"FINISHED"}


def _listing_for(uid: str):
    """The sidebar's copy of the listing, when it already has one."""
    state = browser.STATE
    if state.detail is not None and state.detail.listing.uid == uid:
        return state.detail.listing
    return next((item for item in state.items if item.uid == uid), None)


class FABBRIDGE_OT_toggle_files(bpy.types.Operator):
    """Show or hide the downloadable tiers under the selected asset"""

    bl_idname = "fab_bridge.toggle_files"
    bl_label = "Toggle Files"

    def execute(self, context):
        browser.STATE.files_open = not browser.STATE.files_open
        platform.redraw()
        return {"FINISHED"}


CLASSES = (
    FABBRIDGE_OT_login,
    FABBRIDGE_OT_login_paste,
    FABBRIDGE_OT_cancel_login,
    FABBRIDGE_OT_login_clipboard,
    FABBRIDGE_OT_sign_out,
    FABBRIDGE_OT_check_session,
    FABBRIDGE_OT_open_network_prefs,
    FABBRIDGE_OT_open_fab,
    FABBRIDGE_OT_search,
    FABBRIDGE_OT_load_more,
    FABBRIDGE_OT_select_listing,
    FABBRIDGE_OT_toggle_files,
    FABBRIDGE_OT_set_type,
    FABBRIDGE_OT_toggle_tag,
    FABBRIDGE_OT_clear_filters,
    FABBRIDGE_OT_download,
    FABBRIDGE_OT_cancel_download,
    FABBRIDGE_OT_clear_downloads,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
