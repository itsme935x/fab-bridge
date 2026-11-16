"""Preview-cache wiring (SPEC.md §12.5).

Blender 5.x stopped exposing `bpy.utils.previews` as an attribute of `bpy.utils`
(`from bpy.utils import previews` is required), and a bad image must never take
down the main-thread pump. Both are cheap to regress, so pin them here.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest

from .stub import install_stub, uninstall_stub


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-prev-")
        self.bpy = install_stub(self.tmp)

    def tearDown(self):
        uninstall_stub()

    def _previews(self):
        from fab_bridge.ui import previews

        return previews

    def test_collection_uses_the_previews_submodule(self):
        previews = self._previews()
        self.assertIsNotNone(previews.collection())
        # The stub registers `bpy.utils.previews` in sys.modules, never as an
        # attribute of `bpy` itself, which is exactly Blender's 5.x layout.
        self.assertIn("bpy.utils.previews", sys.modules)

    def test_load_stores_the_icon_id_once(self):
        previews = self._previews()
        path = os.path.join(self.tmp, "thumb.jpg")
        open(path, "wb").close()

        previews._load("key", path)
        previews._load("key", path)  # idempotent

        self.assertEqual(previews._loaded, {"key": 1})
        self.assertNotIn("key", previews._pending)

    def test_a_failing_load_is_recorded_not_raised(self):
        previews = self._previews()
        previews.collection = lambda: (_ for _ in ()).throw(RuntimeError("no previews"))

        previews._load("bad", os.path.join(self.tmp, "missing.jpg"))

        self.assertIn("bad", previews._failed)
        self.assertNotIn("bad", previews._pending)
        self.assertEqual(previews._loaded, {})

    def test_failed_download_clears_pending(self):
        previews = self._previews()
        previews._pending.add("dl")
        previews._finish("dl", None, RuntimeError("boom"))

        self.assertIn("dl", previews._failed)
        self.assertNotIn("dl", previews._pending)

    def test_clear_resets_every_table(self):
        previews = self._previews()
        previews.collection()
        previews._loaded["k"] = 3
        previews._pending.add("k")
        previews._failed.add("k")

        previews.clear()

        self.assertIsNone(previews._pcoll)
        self.assertEqual(previews._loaded, {})
        self.assertEqual(previews._pending, set())
        self.assertEqual(previews._failed, set())

    def test_cache_paths_are_immutable_per_url(self):
        previews = self._previews()
        first = previews.path_for("uid-1", "https://x/a.jpg", 320)
        second = previews.path_for("uid-1", "https://x/a.jpg", 320)
        other = previews.path_for("uid-1", "https://x/b.jpg", 320)

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.endswith(".jpg"))
        self.assertEqual(os.path.dirname(first), os.path.dirname(other))


if __name__ == "__main__":
    unittest.main(verbosity=2)
