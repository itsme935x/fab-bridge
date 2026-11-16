"""Session singleton: load, refresh (single-flight), sign in/out.

The rest of the addon asks this module for a token and never touches the store
or the OAuth module directly, which keeps the two login flows (SPEC.md §6.2,
§6.3) interchangeable: both end in `TokenBundle` and hand it to `adopt()`.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Callable, Optional

from ..core import errors, log
from . import oauth, store
from .models import MIN_REFRESH_INTERVAL, TokenBundle

log = log.get("manager")

Listener = Callable[["AuthManager"], None]


class AuthManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bundle: Optional[TokenBundle] = None
        self._last_refresh = 0.0
        self._listeners: list[Listener] = []
        self._timer_registered = False
        self._refresh_inflight = False

    # -- lifecycle ---------------------------------------------------------
    def setup(self) -> None:
        with self._lock:
            self._bundle = store.load(expected_client_id=None)
        if self._bundle:
            log.info("restored session for %s", self._bundle.display_name or "?")
        self._register_timer()
        self._notify()

    def teardown(self) -> None:
        if not self._timer_registered:
            return
        try:
            import bpy  # noqa: PLC0415

            if bpy.app.timers.is_registered(self._tick):
                bpy.app.timers.unregister(self._tick)
        except Exception:  # pragma: no cover - interpreter shutdown
            pass
        self._timer_registered = False

    # -- observers ---------------------------------------------------------
    def subscribe(self, fn: Listener) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def unsubscribe(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn(self)
            except Exception:  # noqa: BLE001 - a bad listener must not break auth
                log.exception("auth listener failed")

    # -- state -------------------------------------------------------------
    @property
    def signed_in(self) -> bool:
        with self._lock:
            return self._bundle is not None

    def bundle(self) -> Optional[TokenBundle]:
        with self._lock:
            return self._bundle

    def status(self) -> str:
        with self._lock:
            if not self._bundle:
                return "Signed out"
            name = self._bundle.display_name or self._bundle.account_id or "unknown"
            if self._bundle.can_refresh():
                return f"Signed in as {name}"
            return f"Signed in as {name} (session ending)"

    def expires_in(self) -> float:
        with self._lock:
            if not self._bundle:
                return 0.0
            return max(0.0, self._bundle.expires_at - time.time())

    # -- flows -------------------------------------------------------------
    def adopt(self, bundle: TokenBundle) -> None:
        """Accept a bundle from either login flow and persist it."""
        with self._lock:
            self._bundle = bundle
            self._last_refresh = time.time()
            store.save(bundle)
        log.info("signed in as %s", bundle.display_name or bundle.account_id or "?")
        self._notify()

    def sign_in_with_code(self, code: str) -> TokenBundle:
        bundle = oauth.exchange_code(code)
        self.adopt(bundle)
        return bundle

    def sign_out(self, *, remote: bool = True) -> None:
        with self._lock:
            bundle, self._bundle = self._bundle, None
            self._last_refresh = 0.0
        store.clear()
        if bundle and remote:
            oauth.kill_session(bundle.access_token)
        log.info("signed out")
        self._notify()

    # -- tokens ------------------------------------------------------------
    def access_token(self, *, min_valid: int = 60) -> str:
        """Return a usable bearer token, refreshing first if needed.

        Raises AuthRequired when signed out and AuthExpired when the refresh
        window has closed — both are user-visible states, not crashes.
        """
        deadline = time.time() + 30
        while True:
            expired = False
            with self._lock:
                bundle = self._bundle
                if not bundle:
                    raise errors.AuthRequired()

                if bundle.expires_at - time.time() > min_valid:
                    return bundle.access_token

                if not bundle.can_refresh():
                    self._bundle = None
                    expired = True
                elif self._refresh_inflight:
                    pass  # another thread is refreshing; wait for it below
                elif time.time() - self._last_refresh < MIN_REFRESH_INTERVAL:
                    # Asked again too soon after the last refresh; use what we have.
                    return bundle.access_token
                else:
                    self._refresh_inflight = True
                    break  # we own the refresh

            if expired:
                store.clear()
                self._notify()
                raise errors.AuthExpired()

            if time.time() > deadline:
                raise errors.NetworkError("token refresh timed out")
            time.sleep(0.05)

        try:
            return self._do_refresh(bundle)
        finally:
            with self._lock:
                self._refresh_inflight = False

    def _do_refresh(self, old: TokenBundle) -> str:
        try:
            fresh = oauth.refresh(old)
        except errors.AuthExpired:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("refresh failed: %s", type(exc).__name__)
            with self._lock:
                self._bundle = None
            store.clear()
            self._notify()
            raise errors.AuthExpired(str(exc)) from exc

        with self._lock:
            self._bundle = fresh
            self._last_refresh = time.time()
        store.save(fresh)
        log.info("token refreshed")
        self._notify()
        return fresh.access_token

    # -- timer -------------------------------------------------------------
    def _register_timer(self) -> None:
        try:
            import bpy  # noqa: PLC0415

            if not bpy.app.timers.is_registered(self._tick):
                bpy.app.timers.register(self._tick, first_interval=30.0, persistent=True)
            self._timer_registered = True
        except Exception:  # pragma: no cover - background mode
            self._timer_registered = False

    def _tick(self) -> float:
        """Background refresh so the user never waits on a dead token."""
        try:
            bundle = self.bundle()
            if bundle and bundle.needs_refresh() and bundle.can_refresh():
                threading.Thread(
                    target=self._refresh_quietly, name="fab-refresh", daemon=True
                ).start()
        except Exception:  # noqa: BLE001
            log.exception("refresh tick failed")
        return 60.0

    def _refresh_quietly(self) -> None:
        try:
            self.access_token()
        except Exception as exc:  # noqa: BLE001
            log.debug("background refresh stopped: %s", type(exc).__name__)

    def invalidate(self) -> None:
        """Force the next `access_token()` call to refresh (used after a 401)."""
        with self._lock:
            if self._bundle:
                self._bundle = replace(self._bundle, expires_at=0.0)
                self._last_refresh = 0.0


MANAGER = AuthManager()
