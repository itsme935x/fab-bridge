"""Draw the panels against a fake UILayout.

Catches the mistakes that would otherwise only appear when Blender opens the
sidebar: missing operators, bad icon names, attribute errors in card layout.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest

from .stub import install_stub, uninstall_stub
from .test_models import search_result


class FakeProps:
    """Records the RNA properties an operator call sets (`get.quality = …`)."""

    def __init__(self, sink=None):
        object.__setattr__(self, "_sink", sink)

    def __setattr__(self, name, value):
        sink = getattr(self, "_sink", None)
        if sink is not None:
            sink.append(("attr", name, value))
        object.__setattr__(self, name, value)


class FakeSettings:
    default_sort = "-relevance"
    importable_only = True
    thumbnail_columns = "auto"


class FakeLayout:
    def __init__(self, sink, depth=0):
        self.sink = sink
        self.depth = depth
        self.enabled = True
        self.alert = False
        self.scale_y = 1.0
        self.alignment = "LEFT"

    # containers ---------------------------------------------------------
    def row(self, align=False):
        self.sink.append(("row", align, self.depth))
        return FakeLayout(self.sink, self.depth + 1)

    def column(self, align=False):
        return FakeLayout(self.sink, self.depth + 1)

    def box(self):
        return FakeLayout(self.sink, self.depth + 1)

    def grid_flow(self, **kwargs):
        self.sink.append(("grid", kwargs, self.depth))
        return FakeLayout(self.sink, self.depth + 1)

    def split(self, factor=0.5):
        return FakeLayout(self.sink, self.depth + 1)

    # items --------------------------------------------------------------
    def label(self, text="", icon="NONE"):
        self.sink.append(("label", text, icon))
        return self

    def prop(self, owner, attr, **kwargs):
        self.sink.append(("prop", attr, kwargs.get("text")))
        return self

    def operator(self, idname, **kwargs):
        self.sink.append(("op", idname, kwargs.get("text")))
        return FakeProps(self.sink)

    def template_icon(self, icon_value=0, scale=1.0):
        self.sink.append(("icon", icon_value, scale))
        return self

    def separator(self):
        self.sink.append(("sep",))


class FakeScene:
    fab_bridge_query = "rock"


class FakeContext:
    def __init__(self):
        self.scene = FakeScene()


def draw(cls, sink):
    panel = cls.__new__(cls)
    panel.layout = FakeLayout(sink)
    panel.draw(FakeContext())
    return sink


class PanelDrawTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-panel-")
        install_stub(self.tmp)
        self.panel = importlib.import_module("fab_bridge.ui.panel")
        self.browser = importlib.import_module("fab_bridge.ui.browser")
        self.models = importlib.import_module("fab_bridge.api.models")

        self.panel.prefs.get = lambda context=None: FakeSettings()
        self.browser.ensure_started = lambda **kwargs: None
        self.browser.ensure_tag_groups = lambda: None
        self.panel.previews.icon = lambda uid, image: 0
        self.panel.previews.hero = lambda listing: 0
        from types import SimpleNamespace

        self.panel.downloads.MANAGER.jobs = []
        self.panel.downloads.MANAGER.index = lambda root=None: SimpleNamespace(
            downloaded_count=lambda: 0, total_bytes=lambda: 0
        )

        # A signed-out, freshly opened panel state.
        self.browser.reset()
        self.browser.STATE.started = True
        self.browser.STATE.total = 22091
        self.browser.STATE.facets = {"material": 11053, "3d-model": 8815, "brush": 0}
        self.browser.STATE.items = [
            self.models.Listing.parse(search_result(uid="a", title="Asphalt Road")),
            self.models.Listing.parse(
                search_result(
                    uid="b",
                    title="UE Brush",
                    listingType="brush",
                    assetFormats=[{"assetFormatType": {"code": "uefn", "name": "UEFN"}}],
                )
            ),
        ]
        self.browser.STATE.cursor = "next"
        self.browser.STATE.tag_groups = [
            {"name": "Rendering Style", "slug": "rendering-style",
             "tags": [{"name": "Stylized", "slug": "stylized"},
                      {"name": "Realistic", "slug": "realistic"}]},
            {"name": "Unreal Engine", "slug": "unreal-engine",
             "tags": [{"name": "Nanite", "slug": "nanite"}]},
        ]

    def tearDown(self):
        self.panel.downloads.MANAGER.jobs = []
        self.browser.reset()
        uninstall_stub()

    def ops(self, sink):
        return [entry[1] for entry in sink if entry[0] == "op"]

    def test_account_panel_signed_out(self):
        sink = draw(self.panel.FABBRIDGE_PT_main, [])
        ops = self.ops(sink)
        self.assertIn("fab_bridge.login", ops)
        self.assertIn("fab_bridge.login_paste", ops)
        # "Check session" only makes sense once there is a session to check.
        self.assertNotIn("fab_bridge.check_session", ops)
        labels = " ".join(entry[1] for entry in sink if entry[0] == "label")
        self.assertIn("JSON page", labels)
        self.assertIn("Ctrl+A", labels)

    def test_account_panel_explains_the_waiting_login(self):
        import time as _time

        self.panel.ops.LOGIN.update(active=True, started=_time.monotonic())
        try:
            sink = draw(self.panel.FABBRIDGE_PT_main, [])
        finally:
            self.panel.ops.LOGIN["active"] = False
        ops = self.ops(sink)
        self.assertIn("fab_bridge.cancel_login", ops)
        self.assertIn("fab_bridge.login_paste", ops)
        labels = " ".join(entry[1] for entry in sink if entry[0] == "label")
        self.assertIn("Waiting for the Epic code", labels)
        self.assertIn("Ctrl+A", labels)
        self.assertIn("Blender does the rest", labels)

    def test_account_panel_explains_the_auto_capture(self):
        import time as _time

        self.panel.ops.LOGIN.update(active=True, started=_time.monotonic(), mode="browser")
        try:
            sink = draw(self.panel.FABBRIDGE_PT_main, [])
        finally:
            self.panel.ops.LOGIN.update(active=False, mode="clipboard")
        ops = self.ops(sink)
        self.assertIn("fab_bridge.cancel_login", ops)
        # The listener is a bonus; the check list still has to explain copying.
        self.assertIn("fab_bridge.login_clipboard", ops)
        labels = " ".join(entry[1] for entry in sink if entry[0] == "label")
        self.assertIn("redirect link", labels)
        self.assertIn("copy the page", labels)

    def test_account_panel_signed_in(self):
        from types import SimpleNamespace

        class FakeManager:
            signed_in = True

            def bundle(self):
                return SimpleNamespace(display_name="Test User", account_id="acc")

            def expires_in(self):
                return 3600.0

        real = self.panel.auth.MANAGER
        self.panel.auth.MANAGER = FakeManager()
        try:
            sink = draw(self.panel.FABBRIDGE_PT_main, [])
        finally:
            self.panel.auth.MANAGER = real

        ops = self.ops(sink)
        self.assertIn("fab_bridge.sign_out", ops)
        self.assertIn("fab_bridge.check_session", ops)
        labels = " ".join(entry[1] for entry in sink if entry[0] == "label")
        self.assertIn("Test User", labels)
        self.assertIn("1.0 h left", labels)

    def test_a_dead_login_modal_does_not_wedge_the_panel(self):
        self.panel.ops.LOGIN.update(active=True, started=0.0)  # far in the past
        sink = draw(self.panel.FABBRIDGE_PT_main, [])
        self.assertIn("fab_bridge.login", self.ops(sink))
        self.assertNotIn("fab_bridge.cancel_login", self.ops(sink))
        self.assertFalse(self.panel.ops.LOGIN["active"])

    def test_account_panel_blocks_when_online_access_is_off(self):
        self.panel.platform.online_access = lambda: False
        sink = draw(self.panel.FABBRIDGE_PT_main, [])
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertTrue(any("Online access is disabled" in str(text) for text in labels))
        # The banner must lead the user straight to the switch.
        self.assertIn("fab_bridge.open_network_prefs", self.ops(sink))

    def test_search_panel(self):
        sink = draw(self.panel.FABBRIDGE_PT_search, [])
        self.assertIn(("prop", "fab_bridge_query", ""), sink)
        self.assertIn("fab_bridge.search", self.ops(sink))
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertTrue(any("22,091 assets" in str(text) for text in labels))
        # The importable filter is a checkbox, not a primary-looking toggle.
        self.assertIn(("prop", "importable_only", "Blender-ready"), sink)

    def test_clear_chips_button_is_hidden_for_text_only_queries(self):
        self.browser.STATE.query = self.browser.STATE.query.replaced(q="asphalt")
        sink = draw(self.panel.FABBRIDGE_PT_search, [])
        self.assertNotIn("fab_bridge.clear_filters", self.ops(sink))

        self.browser.STATE.query = self.browser.STATE.query.replaced(tags=("stylized",))
        sink = draw(self.panel.FABBRIDGE_PT_search, [])
        self.assertIn("fab_bridge.clear_filters", self.ops(sink))

    def test_search_panel_shows_errors(self):
        self.browser.STATE.items = []
        self.browser.STATE.error = "Fab is unreachable."
        sink = draw(self.panel.FABBRIDGE_PT_search, [])
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertIn("Fab is unreachable.", labels)

    def test_filters_panel_type_and_tag_chips(self):
        sink = draw(self.panel.FABBRIDGE_PT_filters, [])
        ops = self.ops(sink)
        self.assertIn("fab_bridge.set_type", ops)
        self.assertIn("fab_bridge.toggle_tag", ops)
        # Unreal-only tag groups never render in a Blender sidebar.
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertNotIn("Nanite", labels)

    def test_results_panel_cards_and_load_more(self):
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        ops = self.ops(sink)
        # One Formats button per importable card; the UE-only card gets none.
        self.assertEqual(ops.count("fab_bridge.select_listing"), 1)
        self.assertEqual(ops.count("fab_bridge.open_fab"), 2)
        self.assertIn("fab_bridge.load_more", ops)
        texts = [entry[2] for entry in sink if entry[0] == "op"]
        self.assertIn("Formats", texts, "each card must expose a visible action")
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertIn("Not for Blender", labels)
        self.assertTrue(any("3D model" in str(text) or "3D" in str(text) for text in labels))

    def test_results_panel_end_of_results_disables_button(self):
        self.browser.STATE.cursor = None
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        self.assertIn("fab_bridge.load_more", self.ops(sink))

    def test_results_empty_state_offers_a_way_out(self):
        self.browser.STATE.items = []
        self.browser.STATE.query = self.browser.STATE.query.replaced(tags=("stylized",))
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        self.assertIn("fab_bridge.clear_filters", self.ops(sink))
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertIn("Nothing matches those filters.", labels)

    def test_results_are_one_full_width_card_by_default(self):
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        # Cards are drawn straight into the panel, so the only top-level row is
        # "Load more"; nothing wraps two cards into one row.
        top_rows = [entry for entry in sink if entry[0] == "row" and entry[2] == 0]
        self.assertEqual(len(top_rows), 1)

    def test_results_honour_an_explicit_column_preference(self):
        settings = FakeSettings()
        settings.thumbnail_columns = "2"
        self.panel.prefs.get = lambda context=None: settings
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        # One top-level card row (two items) plus Load more. Card-internal rows
        # are nested one level deeper and do not count.
        top_rows = [entry for entry in sink if entry[0] == "row" and entry[2] == 0]
        self.assertEqual(len(top_rows), 2)

    def _select(self, files, index=0):
        listing = self.browser.STATE.items[index]
        self.browser.STATE.selected_uid = listing.uid
        self.browser.STATE.detail = self.browser.Detail(listing=listing, files=files)
        return listing

    def _files(self, code="converted-files", name="Converted Files", files=None):
        return self.models.FormatFiles.parse(
            {
                "assetFormatType": {"code": code, "name": name},
                "files": files
                or [
                    {"uid": "1", "name": "asphalt_high.zip", "size": 112_487_573},
                    {"uid": "2", "name": "asphalt_low.zip", "size": 9_227_395},
                ],
            }
        )

    def test_the_selected_card_opens_its_details_below_itself(self):
        files = self._files()
        self._select(files)

        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        # Asphalt Road card -> its actions -> the UE-only card that follows.
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertLess(labels.index("Asphalt Road"), labels.index("Sign in to download."))
        self.assertLess(labels.index("Sign in to download."), labels.index("UE Brush"))

        ops = self.ops(sink)
        # Folded files: only the primary action, plus the fold button.
        self.assertEqual(ops.count("fab_bridge.download"), 1)
        self.assertIn("fab_bridge.toggle_files", ops)
        self.assertEqual(ops.count("fab_bridge.open_fab"), 3)  # 2 cards + detail

        grids = [entry[1] for entry in sink if entry[0] == "grid"]
        self.assertEqual(grids[-1]["columns"], 2, "formats wrap two per row")
        attrs = [(entry[1], entry[2]) for entry in sink if entry[0] == "attr"]
        self.assertIn(("format_code", "converted-files"), attrs)

    def test_the_files_fold_lists_texture_resolutions(self):
        files = self._files()
        self._select(files)
        self.browser.STATE.files_open = True

        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        ops = self.ops(sink)
        self.assertEqual(ops.count("fab_bridge.download"), 3)  # primary + 2 Get
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertIn("Low · 1K", labels)
        self.assertIn("High · 4K", labels)
        self.assertIn("112 MB", labels)
        texts = [entry[2] for entry in sink if entry[0] == "op"]
        self.assertIn("Files (1K–4K)", texts)
        self.assertEqual(texts.count("Get"), 2)
        attrs = [(entry[1], entry[2]) for entry in sink if entry[0] == "attr"]
        self.assertIn(("format_code", "converted-files"), attrs)
        self.assertIn(("quality", "high"), attrs)
        self.assertIn(("quality", "low"), attrs)

    def test_a_surface_filename_shows_its_own_resolution(self):
        files = self._files(
            code="texture-set",
            name="Texture Set",
            files=[{"uid": "1", "name": "Asphalt_Road_2K.zip", "size": 9_227_395}],
        )
        self._select(files)
        self.browser.STATE.files_open = True
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertIn("Mid · 2K", labels)

    def test_no_selection_means_no_detail_block(self):
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        self.assertNotIn("fab_bridge.toggle_files", self.ops(sink))
        self.assertEqual(self.ops(sink).count("fab_bridge.download"), 0)

    def test_the_primary_button_pins_the_shown_format(self):
        files = self._files(code="texture-set", name="Texture Set",
                            files=[{"uid": "1", "name": "asphalt_1k.zip", "size": 9_227_395}])
        listing = self._select(files)
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        attrs = [(entry[1], entry[2]) for entry in sink if entry[0] == "attr"]
        self.assertIn(("format_code", "texture-set"), attrs)
        self.assertIn(("uid", listing.uid), attrs)

    def test_a_selected_asset_without_files_explains_why(self):
        listing = self.browser.STATE.items[1]
        self.browser.STATE.selected_uid = listing.uid
        self.browser.STATE.detail = self.browser.Detail(listing=listing, files=None)
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertTrue(any("no Blender-importable files" in str(text) for text in labels))
        self.assertNotIn("fab_bridge.toggle_files", self.ops(sink))

    def test_diagnostics_panel(self):
        sink = draw(self.panel.FABBRIDGE_PT_diagnostics, [])
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertTrue(any("schema" in str(text) for text in labels))

    def test_queue_panel_empty_state(self):
        sink = draw(self.panel.FABBRIDGE_PT_queue, [])
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertIn("Nothing downloaded yet.", labels)

    def test_queue_panel_shows_progress_and_a_cancel_button(self):
        downloads = self.panel.downloads
        job = downloads.Job(
            uid="a",
            title="Asphalt Road",
            quality="high",
            size=100,
            done_bytes=71,
            speed=8_200_000,
            stage=downloads.DOWNLOADING,
        )
        downloads.MANAGER.jobs = [job]

        sink = draw(self.panel.FABBRIDGE_PT_queue, [])
        self.assertIn("fab_bridge.cancel_download", self.ops(sink))
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertIn("Downloading", labels)
        self.assertIn("71%", labels)
        self.assertTrue(any("█" in str(text) for text in labels), "a text progress bar is drawn")

    def test_queue_panel_offers_clear_for_finished_jobs(self):
        downloads = self.panel.downloads
        downloads.MANAGER.jobs = [
            downloads.Job(uid="a", title="Asphalt Road", stage=downloads.DONE,
                          summary="Imported 1 object")
        ]
        sink = draw(self.panel.FABBRIDGE_PT_queue, [])
        self.assertIn("fab_bridge.clear_downloads", self.ops(sink))
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertIn("Imported 1 object", labels)
        self.assertNotIn("fab_bridge.cancel_download", self.ops(sink))

    def test_the_selected_asset_asks_signed_out_users_to_sign_in(self):
        listing = self.browser.STATE.items[0]
        files = self.models.FormatFiles.parse(
            {
                "assetFormatType": {"code": "converted-files", "name": "Converted Files"},
                "files": [{"uid": "1", "name": "asphalt_high.zip", "size": 112_487_573}],
            }
        )
        self.browser.STATE.selected_uid = listing.uid
        self.browser.STATE.detail = self.browser.Detail(listing=listing, files=files)
        sink = draw(self.panel.FABBRIDGE_PT_results, [])
        labels = [entry[1] for entry in sink if entry[0] == "label"]
        self.assertTrue(any("Sign in to download" in str(text) for text in labels))


class PrefsDrawTests(unittest.TestCase):
    """The prefs UI must not offer controls that do nothing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-prefs-")
        install_stub(self.tmp)
        self.prefs = importlib.import_module("fab_bridge.ui.prefs")

    def tearDown(self):
        uninstall_stub()

    def draw(self):
        klass = self.prefs.FABBRIDGE_AddonPreferences
        sink = []
        prefs = klass.__new__(klass)
        prefs.layout = FakeLayout(sink)
        prefs.draw(FakeContext())
        return [entry[1] for entry in sink if entry[0] == "prop"]

    def test_wired_controls_are_drawn(self):
        drawn = self.draw()
        for attr in (
            "library_root",
            "keep_archives",
            "default_quality_surface",
            "prefer_format",
            "flip_normals",
            "tiling_mode",
            "displacement",
            "add_subdivision",
            "pack_images",
            "default_sort",
            "importable_only",
            "thumbnail_columns",
            "hero_width",
            "log_level",
            "auto_capture",
        ):
            self.assertIn(attr, drawn)

    def test_unused_controls_are_hidden(self):
        drawn = self.draw()
        for attr in (
            "cache_limit_gb",
            "write_asset_blend",
            "import_all_variations",
            "max_workers",
            "experimental_pkce",
        ):
            self.assertNotIn(attr, drawn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
