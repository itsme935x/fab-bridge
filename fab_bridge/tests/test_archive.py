"""ZIP safety, texture discovery and metadata (SPEC.md §8.2, §10.2).

The zips are built in a temp folder so every case is a real archive, not a mock.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fab_bridge.core import errors  # noqa: E402
from fab_bridge.download import archive  # noqa: E402


def write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return path


class SafeExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-zip-")

    def test_extracts_a_normal_pack(self):
        zip_path = write_zip(
            os.path.join(self.tmp, "ok.zip"),
            {"Textures/albedo.png": b"a", "tiggcjdo.json": b"{}"},
        )
        dest = os.path.join(self.tmp, "out")
        written = archive.safe_extract(zip_path, dest)
        self.assertEqual(len(written), 2)
        self.assertTrue(os.path.exists(os.path.join(dest, "Textures", "albedo.png")))

    def test_rejects_dotdot_traversal(self):
        zip_path = write_zip(
            os.path.join(self.tmp, "evil.zip"), {"../../evil.txt": b"nope"}
        )
        with self.assertRaises(errors.ZipCorrupt):
            archive.safe_extract(zip_path, os.path.join(self.tmp, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_rejects_an_absolute_path(self):
        zip_path = write_zip(
            os.path.join(self.tmp, "abs.zip"), {"/etc/passwd": b"nope"}
        )
        with self.assertRaises(errors.ZipCorrupt):
            archive.safe_extract(zip_path, os.path.join(self.tmp, "out"))

    def test_rejects_symlinks(self):
        zip_path = os.path.join(self.tmp, "link.zip")
        with zipfile.ZipFile(zip_path, "w") as bundle:
            info = zipfile.ZipInfo("alias")
            info.external_attr = (0o120777 << 16)
            bundle.writestr(info, "target")
        with self.assertRaises(errors.ZipCorrupt):
            archive.safe_extract(zip_path, os.path.join(self.tmp, "out"))

    def test_skips_macos_resource_forks(self):
        zip_path = write_zip(
            os.path.join(self.tmp, "mac.zip"),
            {"__MACOSX/._x.png": b"junk", "x.png": b"real"},
        )
        written = archive.safe_extract(zip_path, os.path.join(self.tmp, "out"))
        self.assertEqual([os.path.basename(path) for path in written], ["x.png"])


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-verify-")

    def test_accepts_a_real_zip(self):
        archive.verify_zip(write_zip(os.path.join(self.tmp, "a.zip"), {"a.txt": b"a"}))

    def test_rejects_garbage_without_decompressing_everything(self):
        path = os.path.join(self.tmp, "truncated.zip")
        with open(path, "wb") as handle:
            handle.write(b"PK\x03\x04 but not really")
        with self.assertRaises(errors.ZipCorrupt):
            archive.verify_zip(path)

    def test_rejects_an_empty_archive(self):
        path = os.path.join(self.tmp, "empty.zip")
        with zipfile.ZipFile(path, "w"):
            pass
        with self.assertRaises(errors.ZipCorrupt):
            archive.verify_zip(path)


class TextureDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fabbridge-maps-")

    def touch(self, name, size=10):
        path = os.path.join(self.root, "Textures", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def test_channel_tokens(self):
        cases = {
            "asphalt_road_albedo_4k": "albedo",
            "asphalt_road_BaseColor_4k": "albedo",
            "Asphalt_Rough_4k": "roughness",
            "asphalt_ao_4k": "ao",
            "asphalt_normal_4k": "normal",
            "asphalt_metalness_4k": "metal",
            "asphalt_displacement_4k": "displacement",
            "leaf_opacity_2k": "opacity",
            "asphalt_specular_2k": "specular",
            "rock_bump_2k": "bump",
            "not_a_channel": None,
        }
        for stem, expected in cases.items():
            self.assertEqual(archive.channel_of(stem), expected, stem)

    def test_find_textures_prefers_the_highest_resolution(self):
        self.touch("asphalt_albedo_2k.jpg", 100)
        self.touch("asphalt_albedo_4k.jpg", 50)
        self.touch("asphalt_normal_4k.jpg")
        maps = archive.find_textures(self.root)
        self.assertEqual(os.path.basename(maps["albedo"]), "asphalt_albedo_4k.jpg")
        self.assertIn("normal", maps)

    def test_metadata_and_physical_size(self):
        with open(os.path.join(self.root, "tiggcjdo.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "displacementScale": "0.75",
                    "displacementBias": "-0.2",
                    "physicalSize": "4x2",
                },
                handle,
            )
        metadata = archive.read_metadata(self.root)
        self.assertEqual(metadata["displacementScale"], "0.75")
        self.assertEqual(archive.physical_size(metadata), (4.0, 2.0))
        self.assertEqual(archive.physical_size({}), (2.0, 2.0))

    def test_ignores_json_without_megascans_keys(self):
        with open(os.path.join(self.root, "package.json"), "w", encoding="utf-8") as handle:
            json.dump({"name": "something else"}, handle)
        self.assertEqual(archive.read_metadata(self.root), {})


class ImportFileTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fabbridge-mesh-")

    def write(self, relative, size=10):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def test_converted_files_prefers_glb_over_obj(self):
        self.write("Source/rock.fbx", 200)
        self.write("Source/rock.obj", 300)
        self.write("Source/rock.glb", 100)
        found = archive.find_import_file(self.root, "converted-files")
        self.assertTrue(found.endswith(".glb"), found)

    def test_converted_files_accepts_usdz(self):
        # Fab's smallest "converted files" packs are USDZ, not glTF.
        self.write("Source/rock.usdz", 100)
        found = archive.find_import_file(self.root, "converted-files")
        self.assertTrue(found.endswith(".usdz"), found)

    def test_texture_sets_have_no_mesh(self):
        self.write("Textures/albedo.png")
        self.assertIsNone(archive.find_import_file(self.root, "texture-set"))

    def test_missing_mesh_returns_none(self):
        self.assertIsNone(archive.find_import_file(self.root, "fbx"))


class SafeNameTests(unittest.TestCase):
    def test_strips_path_and_invalid_characters(self):
        self.assertEqual(archive.safe_name('Rock: "Moss"/01'), "Rock Moss01")

    def test_windows_reserved_names_get_a_prefix(self):
        self.assertEqual(archive.safe_name("NUL"), "_NUL")
        self.assertEqual(archive.safe_name("con.zip"), "_con.zip")

    def test_empty_falls_back(self):
        self.assertEqual(archive.safe_name("   ", "asset"), "asset")


if __name__ == "__main__":
    unittest.main(verbosity=2)
