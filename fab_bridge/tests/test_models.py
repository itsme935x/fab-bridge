"""Parsing and wire-format tests (SPEC.md §5.3, §5.4). No bpy, no network."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fab_bridge.api import endpoints as ep  # noqa: E402
from fab_bridge.api.models import (  # noqa: E402
    FormatFiles,
    Listing,
    SearchPage,
    parse_quality,
    pick_image,
    resolution_label,
)
from fab_bridge.api.query import Query, parse_facets  # noqa: E402


def search_result(**overrides):
    raw = {
        "uid": "8a8981c4-bb94-4184-84b7-6c70d7c75ef1",
        "title": "Forest Terrain",
        "listingType": "3d-model",
        "productType": "3d-model",
        "isFree": False,
        "isQuixelClaimable": True,
        "category": {
            "uid": "d9b5d9d3",
            "name": "Forest",
            "path": "nature-plants/forest",
            "slug": "nature-plants--forest",
        },
        "tags": [{"name": "Rock", "slug": "rock"}, {"name": "Dry", "slug": "dry"}],
        "licenses": [{"name": "Personal", "uid": "abc", "priceTier": "tier"}],
        "assetFormats": [
            {"assetFormatType": {"code": "fbx", "name": "FBX", "groupName": "3D Exchange Formats"}},
            {"assetFormatType": {"code": "converted-files", "name": "Converted Files",
                                 "extensions": ["gltf", "glb", "usdz"]}},
            {"assetFormatType": {"code": "uefn", "name": "UEFN Reference"}},
        ],
        "thumbnails": [
            {
                "type": "thumbnail",
                "images": [
                    {"url": "https://media/144.jpg", "width": 144},
                    {"url": "https://media/320.jpg", "width": 320},
                    {"url": "https://media/1280.jpg", "width": 1280},
                ],
            }
        ],
        "user": {"sellerName": "Quixel Megascans", "sellerId": ep.QUIXEL_SELLER},
    }
    raw.update(overrides)
    return raw


class QualityTests(unittest.TestCase):
    def test_surface_resolutions(self):
        self.assertEqual(parse_quality("asphalt_road_tiggcjdo_1k.zip"), "low")
        self.assertEqual(parse_quality("asphalt_road_tiggcjdo_2k.zip"), "mid")
        self.assertEqual(parse_quality("asphalt_road_tiggcjdo_4k.zip"), "high")
        self.assertEqual(parse_quality("asphalt_road_tiggcjdo_8k.zip"), "raw")

    def test_asset_lods(self):
        self.assertEqual(parse_quality("forest_terrain_tletcalfa_low.zip"), "low")
        self.assertEqual(parse_quality("forest_terrain_tletcalfa_gltf_raw.zip"), "raw")
        self.assertEqual(parse_quality("forest_terrain_tletcalfa_raw.glb"), "raw")

    def test_unparsable(self):
        for name in ("", "textures.zip", "atlas_10k.zip", "single_file.fbx"):
            self.assertIsNone(parse_quality(name), name)


class ResolutionLabelTests(unittest.TestCase):
    def test_the_filename_wins(self):
        self.assertEqual(resolution_label("Asphalt_Road_tiggcjdo_2K_BaseColor.jpg"), "2K")
        self.assertEqual(resolution_label("asphalt_road_tiggcjdo_4k.zip"), "4K")

    def test_a_tier_without_a_filename_token_falls_back_to_its_resolution(self):
        self.assertEqual(resolution_label("asphalt_high.zip", "high"), "4K")
        self.assertEqual(resolution_label("forest_raw.glb", "raw"), "8K")

    def test_10k_is_not_mistaken_for_1k(self):
        # `atlas_10k.zip` is not a Megascans tier token, so with no tier there
        # is no label; a `raw` tier still means 8K.
        self.assertEqual(resolution_label("atlas_10k.zip"), "")
        self.assertEqual(resolution_label("atlas_10k.zip", "raw"), "8K")

    def test_unknown_tier_is_blank(self):
        self.assertEqual(resolution_label("single_file.fbx"), "")
        self.assertEqual(resolution_label("", None), "")
        self.assertEqual(resolution_label("photo.jpg", "ultra"), "")


class ListingParseTests(unittest.TestCase):
    def test_core_fields(self):
        listing = Listing.parse(search_result())
        self.assertIsNotNone(listing)
        self.assertEqual(listing.uid, "8a8981c4-bb94-4184-84b7-6c70d7c75ef1")
        self.assertEqual(listing.listing_type, "3d-model")
        self.assertTrue(listing.claimable)
        self.assertFalse(listing.is_free)
        self.assertEqual(listing.category_path, "nature-plants/forest")
        self.assertEqual(listing.tags, ("Rock", "Dry"))
        self.assertTrue(listing.has_format("converted-files"))
        self.assertTrue(listing.seller_id == ep.QUIXEL_SELLER)

    def test_thumbnail_selection(self):
        listing = Listing.parse(search_result())
        self.assertEqual(listing.thumb(320), (320, "https://media/320.jpg"))
        self.assertEqual(listing.thumb(300), (144, "https://media/144.jpg"))
        self.assertEqual(listing.thumb(4000), (1280, "https://media/1280.jpg"))

    def test_format_label_prefers_converted_files(self):
        self.assertEqual(Listing.parse(search_result()).format_label(), "glTF")

    def test_ue_only_listing(self):
        raw = search_result(
            listingType="brush",
            assetFormats=[{"assetFormatType": {"code": "unreal-engine", "name": "Unreal"}}],
        )
        listing = Listing.parse(raw)
        self.assertEqual(listing.format_label(), "UE only")

    def test_gallery_ignores_sketchfab_model_media(self):
        raw = search_result(
            medias=[
                {"type": "model", "images": [{"url": "https://media/poster.jpg", "width": 1060}]},
                {"type": "image", "images": [{"url": "https://media/hero-1280.jpg", "width": 1280}]},
                {"type": "image", "images": [{"url": "https://media/hero-2560.jpg", "width": 2560}]},
            ]
        )
        listing = Listing.parse(raw)
        self.assertEqual(listing.hero(1920), (1280, "https://media/hero-1280.jpg"))
        self.assertNotIn("https://media/poster.jpg", [url for _, url in listing.gallery])

    def test_missing_fields_do_not_fail(self):
        listing = Listing.parse({"uid": "x"})
        self.assertEqual(listing.title, "Untitled")
        self.assertEqual(listing.formats, ())
        self.assertIsNone(listing.thumb())
        self.assertIsNone(Listing.parse({"title": "no uid"}))
        self.assertIsNone(Listing.parse("not a dict"))


class SearchPageTests(unittest.TestCase):
    def test_cursor_comes_from_cursors(self):
        page = SearchPage.parse(
            {
                "results": [search_result()],
                "cursors": {"next": "bz0yNA==", "previous": None},
                "next": "https://www.fab.com/i/listings/search?cursor=bz0yNA%3D%3D",
            }
        )
        self.assertEqual(len(page.listings), 1)
        self.assertEqual(page.cursor, "bz0yNA==")

    def test_bad_results_are_skipped(self):
        page = SearchPage.parse({"results": [None, {}, {"uid": "ok"}], "cursors": {}})
        self.assertEqual([item.uid for item in page.listings], ["ok"])
        self.assertIsNone(page.cursor)


class FormatFilesTests(unittest.TestCase):
    def test_parse_and_order(self):
        raw = {
            "assetFormatType": {"code": "texture-set", "name": "Texture Set", "extensions": ["zip"]},
            "files": [
                {"uid": "a", "name": "asphalt_8k.zip", "size": 280495203, "fileType": "source"},
                {"uid": "b", "name": "asphalt_1k.zip", "size": 9227395, "fileType": "source"},
                {"uid": "c", "name": "asphalt_4k.zip", "size": 112487573, "fileType": "source"},
                {"uid": "d", "name": "asphalt_2k.zip", "size": 33533358, "fileType": "source"},
            ],
            "techDetails": {"material": {}},
        }
        files = FormatFiles.parse(raw)
        self.assertEqual(files.format.code, "texture-set")
        self.assertEqual([f.quality for f in files.ordered()], ["low", "mid", "high", "raw"])
        self.assertEqual(files.files[0].size_label(), "280 MB")

    def test_vertex_counts_and_urls(self):
        raw = {
            "assetFormatType": {"code": "fbx", "name": "FBX"},
            "files": [{
                "uid": "b7216d22", "name": "forest_terrain_tletcalfa_high.zip",
                "size": 72235510, "vertexCount": 6192, "faceCount": 12135,
                "fileType": "source", "status": "ready",
            }],
        }
        entry = FormatFiles.parse(raw).files[0]
        self.assertEqual(entry.vertex_count, 6192)
        self.assertEqual(entry.face_count, 12135)
        self.assertEqual(entry.quality, "high")


class QueryTests(unittest.TestCase):
    def test_defaults(self):
        params = Query().params()
        self.assertIn(("sellers", ep.QUIXEL_SELLER), params)
        self.assertEqual(params.count(("asset_formats", "fbx")), 1)
        self.assertEqual(params.count(("asset_formats", "texture-set")), 1)
        self.assertIn(("count", "24"), params)
        self.assertIn(("sort_by", "-relevance"), params)

    def test_importable_toggle(self):
        params = Query(importable=False).params()
        self.assertFalse(any(key == "asset_formats" for key, _ in params))

    def test_invalid_values_never_reach_the_server(self):
        params = Query(listing_type="nonsense", sort="-averageRating").params()
        keys = [key for key, _ in params]
        self.assertNotIn("listing_types", keys)
        self.assertNotIn("sort_by", keys)

    def test_repeated_params(self):
        query = Query(q="  rock ", category="nature-plants--rocks-stones", tags=("rock", "sandstone"))
        params = query.params(count=0, cursor="abc")
        self.assertIn(("q", "rock"), params)
        self.assertIn(("categories", "nature-plants--rocks-stones"), params)
        self.assertIn(("tags", "rock"), params)
        self.assertIn(("tags", "sandstone"), params)
        self.assertIn(("count", "0"), params)
        self.assertIn(("cursor", "abc"), params)

    def test_facet_params(self):
        params = Query(q="rock").facet_params()
        self.assertEqual(params[-1], ("aggregateOn", "listing_type"))
        self.assertIn(("count", "0"), params)
        self.assertIn(("q", "rock"), params)

    def test_toggle_tag(self):
        query = Query().toggle_tag("rock")
        self.assertEqual(query.tags, ("rock",))
        self.assertEqual(query.toggle_tag("rock").tags, ())

    def test_clear_filters_keeps_text(self):
        query = Query(q="rock", listing_type="material", tags=("rock",))
        cleared = query.clear_filters()
        self.assertEqual(cleared.q, "rock")
        self.assertEqual(cleared.listing_type, "")
        self.assertEqual(cleared.tags, ())

    def test_has_chips_ignores_the_text_query(self):
        self.assertFalse(Query(q="asphalt").has_chips())
        self.assertTrue(Query(q="asphalt").is_filtered())
        self.assertTrue(Query(listing_type="material").has_chips())
        self.assertTrue(Query(category="asphalt--road").has_chips())
        self.assertTrue(Query(tags=("stylized",)).has_chips())


class FacetTests(unittest.TestCase):
    def test_listing_type_buckets(self):
        raw = {
            "aggregations": {
                "listingType": {
                    "buckets": {
                        "material": {"docCount": 11053},
                        "3d-model": {"docCount": 8825},
                        "decal": {"docCount": 2223},
                        "brush": {"docCount": 519},
                    }
                }
            }
        }
        counts = parse_facets(raw)
        self.assertEqual(counts["material"], 11053)
        self.assertEqual(sum(counts.values()), 22620)

    def test_missing_aggregations(self):
        self.assertEqual(parse_facets({"results": []}), {})
        self.assertEqual(parse_facets({"aggregations": None}), {})


class PickImageTests(unittest.TestCase):
    def test_limits(self):
        images = ((144, "a"), (320, "b"), (1280, "c"))
        self.assertEqual(pick_image(images, 320), (320, "b"))
        self.assertEqual(pick_image(images, 100), (144, "a"))
        self.assertIsNone(pick_image((), 320))


if __name__ == "__main__":
    unittest.main(verbosity=2)
