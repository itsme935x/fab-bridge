"""Search/pagination/selection state machine (SPEC.md §4.2 rule 5, §12.2).

Runs against the stub bpy with the network replaced by fixtures; worker threads
are collapsed into inline calls so the generation gating is deterministic.

Every module is re-imported in setUp because `stub.uninstall_stub()` pops the
`fab_bridge` package between tests (each test gets a fresh stub bpy object).
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from unittest import mock

from .stub import install_stub, uninstall_stub
from .test_models import search_result


def inline_spawn(fn, *args, done=None, name="test", **kwargs):
    try:
        result = fn(*args, **kwargs)
        error = None
    except BaseException as exc:  # noqa: BLE001 - mirrors core.tasks
        result, error = None, exc
    if done is not None:
        done(result, error)
    return None


def capture_spawn(calls):
    def spawn(fn, *args, done=None, name="test", **kwargs):
        calls.append(done)
        return None

    return spawn


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-browser-")
        install_stub(self.tmp)
        self.browser = importlib.import_module("fab_bridge.ui.browser")
        self.models = importlib.import_module("fab_bridge.api.models")
        self.errors = importlib.import_module("fab_bridge.core.errors")
        self.query = importlib.import_module("fab_bridge.api.query")
        self.endpoints = importlib.import_module("fab_bridge.api.endpoints")

        self.browser.previews.prefetch = lambda items: None
        self.browser.previews.hero = lambda item: 0
        self.browser.tasks.spawn = inline_spawn
        self.browser.reset()

    def tearDown(self):
        self.browser.reset()
        uninstall_stub()

    # -- fixtures ----------------------------------------------------------
    def listing(self, uid, title="Asset", formats=("converted-files", "fbx")):
        return self.models.Listing.parse(
            search_result(
                uid=uid,
                title=title,
                assetFormats=[
                    {"assetFormatType": {"code": code, "name": code}} for code in formats
                ],
            )
        )

    def page(self, *uids, cursor=None):
        return self.models.SearchPage(
            listings=tuple(self.listing(uid) for uid in uids), cursor=cursor
        )

    # -- tests -------------------------------------------------------------
    def test_search_populates_state_and_total(self):
        page = self.page("a", "b", cursor="next-1")
        with mock.patch.object(self.browser.fab, "search", return_value=page), \
                mock.patch.object(self.browser.fab, "facets", return_value={"material": 22620}):
            self.browser.set_query(self.query.Query(q="rock"))

        state = self.browser.STATE
        self.assertEqual([item.uid for item in state.items], ["a", "b"])
        self.assertEqual(state.cursor, "next-1")
        self.assertEqual(state.total, 22620)
        self.assertFalse(state.loading)
        self.assertEqual(state.error, "")

    def test_facet_failure_does_not_blank_results(self):
        page = self.page("a")
        with mock.patch.object(self.browser.fab, "search", return_value=page), \
                mock.patch.object(
                    self.browser.fab, "facets", side_effect=self.errors.NetworkError("x")
                ):
            self.browser.set_query(self.query.Query())

        state = self.browser.STATE
        self.assertEqual(len(state.items), 1)
        self.assertEqual(state.facets, {})
        self.assertEqual(state.total, 0)

    def test_stale_response_is_dropped(self):
        calls = []
        self.browser.tasks.spawn = capture_spawn(calls)
        self.browser.set_query(self.query.Query(q="first"))
        self.browser.set_query(self.query.Query(q="second"))
        self.assertEqual(len(calls), 2)

        calls[1]((self.page("fresh", cursor="y"), {}), None)
        calls[0]((self.page("stale", cursor="x"), {}), None)  # arrives late: ignored

        state = self.browser.STATE
        self.assertEqual([item.uid for item in state.items], ["fresh"])
        self.assertEqual(state.cursor, "y")

    def test_load_more_appends_and_stops_at_the_end(self):
        first = self.page("a", "b", cursor="c1")
        second = self.page("b", "c")

        def fake_search(query, *, cursor=None):
            return second if cursor == "c1" else first

        with mock.patch.object(self.browser.fab, "search", side_effect=fake_search), \
                mock.patch.object(self.browser.fab, "facets", return_value={}):
            self.browser.set_query(self.query.Query())
            self.browser.load_more()

        state = self.browser.STATE
        self.assertEqual([item.uid for item in state.items], ["a", "b", "c"])  # deduped
        self.assertIsNone(state.cursor)

        self.browser.load_more()  # no cursor: must be a no-op
        self.assertEqual(len(state.items), 3)

    def test_load_more_is_ignored_while_loading(self):
        calls = []
        self.browser.tasks.spawn = capture_spawn(calls)
        self.browser.STATE.loading = True
        self.browser.STATE.cursor = "c1"
        self.browser.load_more()
        self.assertEqual(calls, [])

    def test_select_resolves_preferred_format(self):
        detail = self.models.Listing.parse(
            search_result(
                uid="mat",
                listingType="material",
                assetFormats=[
                    {"assetFormatType": {"code": "gltf", "name": "GLTF"}},
                    {"assetFormatType": {"code": "texture-set", "name": "Texture Set"}},
                ],
            )
        )
        files = self.models.FormatFiles.parse(
            {
                "assetFormatType": {"code": "texture-set", "name": "Texture Set"},
                "files": [
                    {"uid": "1", "name": "asphalt_8k.zip", "size": 280_495_203},
                    {"uid": "2", "name": "asphalt_1k.zip", "size": 9_227_395},
                ],
            }
        )
        with mock.patch.object(self.browser.fab, "get_listing", return_value=detail), \
                mock.patch.object(
                    self.browser.fab, "get_format_files", return_value=files
                ) as fetch:
            self.browser.select("mat")

        fetch.assert_called_once_with("mat", "texture-set")
        state = self.browser.STATE
        self.assertEqual(state.selected_uid, "mat")
        self.assertIsNotNone(state.detail)
        self.assertEqual(state.detail.files.format.code, "texture-set")
        self.assertFalse(state.detail_loading)

    def test_stale_detail_is_dropped(self):
        calls = []
        self.browser.tasks.spawn = capture_spawn(calls)
        self.browser.select("old")
        self.browser.select("new")

        calls[1](self.browser.Detail(listing=self.listing("new")), None)
        calls[0](self.browser.Detail(listing=self.listing("old")), None)

        self.assertEqual(self.browser.STATE.detail.listing.uid, "new")

    def test_error_messages_are_user_facing(self):
        self.assertEqual(
            self.browser.message(self.errors.AuthRequired()),
            self.errors.AuthRequired.user_message,
        )
        self.assertIn("log", self.browser.message(RuntimeError("boom")))

    def test_usable_detects_ue_only_listings(self):
        self.assertTrue(self.browser.usable(self.listing("a")))
        ue_only = self.models.Listing.parse(
            search_result(assetFormats=[{"assetFormatType": {"code": "uefn", "name": "UEFN"}}])
        )
        self.assertFalse(self.browser.usable(ue_only))

    def test_ensure_started_is_idempotent(self):
        self.browser.ensure_started(search=False)
        self.assertTrue(self.browser.STATE.started)
        self.assertEqual(self.browser.STATE.items, [])
        # A second call must not register a second tick or bump the generation.
        generation = self.browser.STATE.generation
        self.browser.ensure_started(search=False)
        self.assertEqual(self.browser.STATE.generation, generation)

    def test_preferred_format_chain(self):
        self.assertEqual(
            self.browser.preferred_format(self.listing("p", formats=("fbx", "gltf"))), "fbx"
        )
        self.assertEqual(
            self.browser.preferred_format(self.listing("p", formats=("uefn", "unreal-engine"))),
            "",
        )

    def test_query_uses_verified_defaults(self):
        self.assertEqual(self.query.Query().seller, self.endpoints.QUIXEL_SELLER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
