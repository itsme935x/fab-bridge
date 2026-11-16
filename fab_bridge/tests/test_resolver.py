"""Format/quality resolution (SPEC.md §7). Pure functions, no bpy, no network."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fab_bridge.api.models import FormatFiles, FormatInfo, Listing  # noqa: E402
from fab_bridge.core import errors  # noqa: E402
from fab_bridge.download import resolver  # noqa: E402


def listing(formats=("texture-set",), listing_type="material", category="", tags=()):
    return Listing(
        uid="df8b577f-4fbe-4757-b2b9-d0e89c62255c",
        title="Asphalt Road",
        listing_type=listing_type,
        category_name=category,
        category_path=category,
        tags=tuple(tags),
        formats=tuple(FormatInfo(code=code, name=code) for code in formats),
    )


def files(code="texture-set", entries=()):
    return FormatFiles(
        format=FormatInfo(code=code, name=code.upper()),
        files=tuple(entry for entry in entries),
    )


def entry(name, size=1000, uid=None):
    from fab_bridge.api.models import AssetFile

    return AssetFile.parse({"uid": uid or name, "name": name, "size": size})


class FormatChainTests(unittest.TestCase):
    def test_texture_sets_never_mix_with_mesh_formats(self):
        surface = listing(("texture-set", "fbx", "gltf"))
        self.assertEqual(resolver.format_chain(surface), ("texture-set",))

    def test_mesh_chain_prefers_converted_files(self):
        prop = listing(("fbx", "gltf", "converted-files"), listing_type="3d-model")
        self.assertEqual(resolver.format_chain(prop)[0], "converted-files")

    def test_plants_prefer_fbx(self):
        plant = listing(("converted-files", "fbx", "gltf"), category="nature-plants/grass")
        self.assertTrue(resolver.is_plant(plant))
        self.assertEqual(resolver.format_chain(plant)[0], "fbx")

    def test_unreal_only_lists_nothing(self):
        ue = listing(("uefn", "unreal-engine"))
        self.assertEqual(resolver.format_chain(ue), ())
        with self.assertRaises(errors.UnsupportedFormat):
            resolver.pick_format(ue)

    def test_pinned_format_wins_when_present(self):
        prop = listing(("fbx", "gltf"), listing_type="3d-model")
        self.assertEqual(resolver.pick_format(prop, "gltf"), "gltf")
        # ...but not when the listing does not have it. The chain (not the
        # listing's own order) then decides, and FBX outranks glTF (§7.1).
        self.assertEqual(resolver.pick_format(prop, "converted-files"), "fbx")


class ResolveTests(unittest.TestCase):
    def test_exact_tier(self):
        plan = resolver.resolve(
            listing(),
            files(entries=(entry("road_1k.zip", 10), entry("road_4k.zip", 40))),
            wanted_quality="high",
        )
        self.assertEqual(plan.quality, "high")
        self.assertEqual(plan.filename, "road_4k.zip")
        self.assertEqual(plan.note, "")
        self.assertIn("High", plan.summary())

    def test_falls_back_to_the_nearest_lower_tier(self):
        plan = resolver.resolve(
            listing(),
            files(entries=(entry("road_1k.zip", 10), entry("road_2k.zip", 20))),
            wanted_quality="raw",
        )
        self.assertEqual(plan.quality, "mid")
        self.assertIn("Raw not available", plan.note)
        self.assertIn("Mid", plan.note)

    def test_falls_back_up_when_nothing_is_lower(self):
        plan = resolver.resolve(
            listing(),
            files(entries=(entry("road_4k.zip", 40), entry("road_8k.zip", 80))),
            wanted_quality="low",
        )
        self.assertEqual(plan.quality, "high")

    def test_asset_tiers_use_low_high_raw_tokens(self):
        plan = resolver.resolve(
            listing(("fbx",), listing_type="3d-model"),
            files("fbx", entries=(entry("tree_low.zip", 5), entry("tree_high.zip", 50))),
            wanted_quality="high",
        )
        self.assertEqual(plan.quality, "high")

    def test_single_file_formats_pick_the_largest_download(self):
        plan = resolver.resolve(
            listing(("fbx",), listing_type="3d-model"),
            files("fbx", entries=(entry("preview.zip", 1), entry("pack.zip", 99))),
        )
        self.assertEqual(plan.filename, "pack.zip")
        self.assertEqual(plan.quality, None)
        self.assertEqual(plan.note, "single file")

    def test_empty_format_is_an_error(self):
        with self.assertRaises(errors.UnsupportedFormat):
            resolver.resolve(listing(), files(entries=()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
