"""Operator-level checks for the login dialog.

Only the parts that need no real window: the paste dialog reads the clipboard
so the user does not have to paste a second time.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest

from .stub import install_stub, uninstall_stub


class FakeWindowManager:
    def __init__(self, clipboard=""):
        self.clipboard = clipboard
        self.dialog = None

    def invoke_props_dialog(self, operator, width=0):
        self.dialog = operator
        return {"RUNNING_MODAL"}


class FakeContext:
    def __init__(self, clipboard=""):
        self.window_manager = FakeWindowManager(clipboard)


class PasteDialogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-ops-")
        install_stub(self.tmp)
        self.ops = importlib.import_module("fab_bridge.ui.ops")

    def tearDown(self):
        uninstall_stub()

    def _invoke(self, clipboard):
        context = FakeContext(clipboard)
        operator = self.ops.FABBRIDGE_OT_login_paste()
        result = operator.invoke(context, None)
        return operator, context, result

    def test_a_json_page_on_the_clipboard_prefills_the_dialog(self):
        page = json.dumps(
            {
                "warning": "Do not share this code.",
                "redirectUrl": "https://localhost/launcher/authorized?code=c9b5185aa90e4e54830b4169116b9142",
                "authorizationCode": "c9b5185aa90e4e54830b4169116b9142",
                "exchangeCode": None,
            }
        )
        operator, context, result = self._invoke(page)
        self.assertEqual(result, {"RUNNING_MODAL"})
        self.assertEqual(operator.code, "c9b5185aa90e4e54830b4169116b9142")
        self.assertIs(context.window_manager.dialog, operator)

    def test_a_clean_clipboard_leaves_the_field_empty(self):
        operator, _, result = self._invoke("just some text")
        self.assertEqual(result, {"RUNNING_MODAL"})
        self.assertFalse(getattr(operator, "code", ""))

    def test_a_jwt_is_not_mistaken_for_a_code(self):
        operator, _, _ = self._invoke("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature")
        self.assertFalse(getattr(operator, "code", ""))


class LoginUrlTests(unittest.TestCase):
    """The sign-in operator must open Epic's code page, not a localhost redirect.

    Epic's account portal drops an unregistered localhost `redirectUrl` and
    leaves a signed-in user on account settings (seen live), so the modal opens
    the documented copy-code URL and keeps the listener as an opportunistic
    extra.
    """

    def setUp(self):
        from types import SimpleNamespace

        self.tmp = tempfile.mkdtemp(prefix="fabbridge-ops-")
        install_stub(self.tmp)
        self.ops = importlib.import_module("fab_bridge.ui.ops")

        self.opened = []
        self.ops.webbrowser.open = lambda url, new=0: bool(self.opened.append(url)) or True
        self.ops.prefs.get = lambda context=None: SimpleNamespace(auto_capture=True)
        self.ops.auth.MANAGER = SimpleNamespace(signed_in=False)

        listener = self.listener = SimpleNamespace(started=False, stopped=False, code="")

        def start():
            listener.started = True
            return True

        def stop():
            listener.stopped = True

        listener.start = start
        listener.stop = stop
        self.ops.loopback.Listener = lambda: listener

        class FakeWM(FakeWindowManager):
            def event_timer_add(self, interval, window=None):
                return object()

            def modal_handler_add(self, operator):
                return None

            def event_timer_remove(self, timer):
                return None

        self.context = FakeContext("")
        self.context.window = object()
        self.context.window_manager = FakeWM("")

    def tearDown(self):
        uninstall_stub()

    def _invoke(self):
        operator = self.ops.FABBRIDGE_OT_login()
        operator.report = lambda level, message: None
        self.assertEqual(operator.invoke(self.context, None), {"RUNNING_MODAL"})
        operator._on_timer(self.context)
        return operator

    def test_login_opens_the_epic_code_page_even_with_auto_capture(self):
        operator = self._invoke()
        try:
            self.assertTrue(self.listener.started)
            self.assertEqual(self.ops.LOGIN["mode"], "browser")
            self.assertEqual(len(self.opened), 1)
            url = self.opened[0]
            self.assertNotIn("localhost", url)
            self.assertIn("%2Fid%2Fapi%2Fredirect", url)
            self.assertIn("clientId%3D" + self.ops.oauth.ep.CLIENT_LAUNCHER[0], url)
        finally:
            operator._cleanup(self.context)
        self.assertFalse(self.ops.LOGIN["active"])
        self.assertTrue(self.listener.stopped)

    def test_a_copied_code_is_read_while_the_listener_is_running(self):
        operator = self._invoke()
        started = []

        class FakeExchange:
            def __init__(self, code):
                started.append(code)

            def start(self):
                return self

            @property
            def done(self):
                return False

        self.ops._Exchange = FakeExchange
        page = json.dumps({"authorizationCode": "c9b5185aa90e4e54830b4169116b9142"})
        self.context.window_manager.clipboard = page
        try:
            self.assertEqual(operator._on_timer(self.context), {"RUNNING_MODAL"})
        finally:
            operator._cleanup(self.context)
        self.assertEqual(started, ["c9b5185aa90e4e54830b4169116b9142"])


class OfflineGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-ops-")
        self.bpy = install_stub(self.tmp)
        self.ops = importlib.import_module("fab_bridge.ui.ops")

    def tearDown(self):
        uninstall_stub()

    def test_paste_dialog_refuses_when_online_access_is_off(self):
        self.bpy.app.online_access = False
        operator = self.ops.FABBRIDGE_OT_login_paste()
        reported = []
        operator.report = lambda level, message: reported.append(message)
        self.assertEqual(operator.invoke(FakeContext(""), None), {"CANCELLED"})
        self.assertTrue(reported)


if __name__ == "__main__":
    unittest.main(verbosity=2)
