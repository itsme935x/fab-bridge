"""Smoke-test the register/unregister wiring with a stub bpy.

This does not prove the panel draws correctly (only Blender can do that), but
it does catch import errors, bad class attributes, and teardown leaks, which is
most of what breaks an extension on enable.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from .stub import install_stub, uninstall_stub


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-ui-")
        self.bpy = install_stub(self.tmp)

    def tearDown(self):
        uninstall_stub()

    def test_register_and_unregister_twice(self):
        import fab_bridge

        fab_bridge.register()
        first = list(self.bpy.utils._classes)
        self.assertGreater(len(first), 5, "expected prefs, operators and panels")

        # Idempotent enable/disable must not raise or leak the timer.
        fab_bridge.unregister()
        self.assertEqual(self.bpy.utils._classes, [])

        fab_bridge.register()
        fab_bridge.unregister()
        self.assertEqual(self.bpy.utils._classes, [])

    def test_prefs_bl_idname_matches_addon(self):
        import fab_bridge
        from fab_bridge.ui import prefs

        fab_bridge.register()
        try:
            self.assertEqual(prefs.FABBRIDGE_AddonPreferences.bl_idname, fab_bridge.ADDON_ID)
        finally:
            fab_bridge.unregister()

    def test_register_does_not_raise_without_online_access(self):
        import fab_bridge

        self.bpy.app.online_access = False
        fab_bridge.register()  # must not raise; the panel shows the warning
        fab_bridge.unregister()

    def test_scene_property_is_removed_on_unregister(self):
        import fab_bridge

        fab_bridge.register()
        self.assertTrue(hasattr(self.bpy.types.Scene, "fab_bridge_query"))
        fab_bridge.unregister()
        self.assertFalse(hasattr(self.bpy.types.Scene, "fab_bridge_query"))

    def test_pump_timer_is_registered_once(self):
        import fab_bridge
        from fab_bridge.core import platform

        fab_bridge.register()
        fab_bridge.register()  # double register must not stack timers
        self.assertTrue(self.bpy.app.timers.is_registered(platform.pump))
        fab_bridge.unregister()
        self.assertFalse(self.bpy.app.timers.is_registered(platform.pump))

    def test_manifest_is_valid_toml(self):
        import tomllib

        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "blender_manifest.toml")
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertEqual(data["type"], "add-on")
        self.assertEqual(data["blender_version_min"], "4.2.0")
        self.assertIn("network", data["permissions"])
        self.assertIn("clipboard", data["permissions"])
        self.assertTrue(data["license"][0].startswith("SPDX:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
