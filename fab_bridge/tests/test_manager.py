"""The download queue end to end (SPEC.md §8), against a local HTTP server.

The CDN is replaced by `http.server` so the streaming, `Range` resume, ZIP
verification, safe extraction and index write are all real. Only the Fab JSON
calls and the import hook are stubbed.
"""

from __future__ import annotations

import http.server
import importlib
import json
import os
import tempfile
import threading
import unittest
import zipfile

from .stub import install_stub, uninstall_stub


PAYLOAD_FILES = {
    "Textures/asphalt_road_albedo_2k.png": b"a" * 512,
    "Textures/asphalt_road_normal_2k.png": b"n" * 512,
    "Textures/asphalt_road_roughness_2k.png": b"r" * 512,
    "tiggcjdo.json": json.dumps(
        {"displacementScale": "0.5", "displacementBias": "0.5", "physicalSize": "2x2"}
    ).encode(),
}


def build_zip(path: str) -> bytes:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as bundle:
        for name, data in PAYLOAD_FILES.items():
            bundle.writestr(name, data)
    with open(path, "rb") as handle:
        return handle.read()


class RangeHandler(http.server.BaseHTTPRequestHandler):
    payload = b""
    requests: list = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        RangeHandler.requests.append(self.headers.get("Range"))
        data = RangeHandler.payload
        header = self.headers.get("Range") or ""
        if header.startswith("bytes=") and header.endswith("-"):
            start = int(header[6:-1])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            chunk = data[start:]
        else:
            self.send_response(200)
            chunk = data
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *args):  # noqa: A003 - silence the test output
        pass


class DownloadManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fabbridge-dl-")
        install_stub(self.tmp)
        self.settings = importlib.import_module("fab_bridge.download.manager")
        self.models = importlib.import_module("fab_bridge.api.models")
        self.errors = importlib.import_module("fab_bridge.core.errors")
        self.tasks = importlib.import_module("fab_bridge.core.tasks")

        self.zip_path = os.path.join(self.tmp, "asset.zip")
        RangeHandler.payload = build_zip(self.zip_path)
        RangeHandler.requests = []
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/asset.zip"

        self.settings.library_root = lambda: self.tmp
        self._real_hosts = self.settings.ALLOWED_HOSTS
        self.settings.ALLOWED_HOSTS = self._real_hosts + ("127.0.0.1",)
        self._real_files = self.settings.fab.get_format_files
        self._real_url = self.settings.fab.download_url
        self.settings.fab.get_format_files = self.files
        self.settings.fab.download_url = self.download_url
        self.imported = []
        self.settings.IMPORT_HOOK = self.hook
        self.manager = self.settings.Manager()

    def tearDown(self):
        self.settings.ALLOWED_HOSTS = self._real_hosts
        self.settings.fab.get_format_files = self._real_files
        self.settings.fab.download_url = self._real_url
        self.settings.IMPORT_HOOK = None
        uninstall_stub()

    # -- fixtures ---------------------------------------------------------
    def listing(self):
        return self.models.Listing(
            uid="df8b577f-4fbe-4757-b2b9-d0e89c62255c",
            title="Asphalt Road",
            listing_type="material",
            seller="Quixel Megascans",
            formats=(self.models.FormatInfo(code="texture-set", name="Texture Set"),),
        )

    def files(self, uid, code):
        return self.models.FormatFiles.parse(
            {
                "assetFormatType": {"code": "texture-set", "name": "Texture Set"},
                "files": [
                    {"uid": "low", "name": "asphalt_road_2k.zip", "size": len(RangeHandler.payload)},
                    {"uid": "high", "name": "asphalt_road_4k.zip", "size": 40},
                ],
            }
        )

    def download_url(self, uid, code, file_uid, require_auth=True):
        self.assert_auth = require_auth
        return self.url

    def hook(self, job):
        self.imported.append(job.uid)
        return "imported by test"

    def job(self, **kwargs):
        values = dict(
            uid="df8b577f-4fbe-4757-b2b9-d0e89c62255c",
            title="Asphalt Road",
            quality="mid",
            format_code="texture-set",
            listing=self.listing(),
        )
        values.update(kwargs)
        return self.settings.Job(**values)

    # -- tests ------------------------------------------------------------
    def test_download_extract_import(self):
        job = self.job()
        self.manager.run(job)

        self.assertEqual(job.stage, self.settings.IMPORTING, (job.stage, job.error, job.note))
        self.assertEqual(job.size, len(RangeHandler.payload))
        self.assertEqual(job.done_bytes, len(RangeHandler.payload))
        self.assertEqual(job.summary, "Extracted 4 files")
        self.assertTrue(os.path.exists(job.directory), job.directory)
        self.assertTrue(
            os.path.exists(os.path.join(job.directory, "Textures", "asphalt_road_albedo_2k.png"))
        )
        # The ZIP is deleted by default; the extracted folder is what lasts.
        self.assertFalse(os.path.exists(job.archive_path) if job.archive_path else False)

        # The import hand-off only happens on the main-thread tick.
        self.assertFalse(self.imported)
        self.assertEqual(job.stage, self.settings.IMPORTING)
        self.tasks.run_pending()
        self.assertEqual(job.stage, self.settings.DONE)
        self.assertEqual(job.summary, "imported by test")
        self.assertEqual(self.imported, [job.uid])

        index = self.manager.index(self.tmp)
        self.assertEqual(index.lookup(job.uid, "texture-set", "mid"), job.directory)
        self.assertEqual(index.downloaded_count(), 1)

    def test_requests_the_bearer_token_even_though_free_files_work_anonymously(self):
        self.manager.run(self.job())
        self.assertTrue(self.assert_auth)

    def test_resumes_a_partial_download_with_a_range_request(self):
        part = os.path.join(
            self.tmp,
            "Asphalt Road [df8b577f]",
            "_archives",
            "mid-asphalt_road_2k.zip.part",
        )
        os.makedirs(os.path.dirname(part), exist_ok=True)
        half = len(RangeHandler.payload) // 2
        with open(part, "wb") as handle:
            handle.write(RangeHandler.payload[:half])

        job = self.job()
        self.manager.run(job)

        self.assertIn(f"bytes={half}-", RangeHandler.requests)
        self.assertEqual(job.stage, self.settings.IMPORTING)
        self.assertEqual(job.done_bytes, len(RangeHandler.payload))

    def test_cancel_before_the_stream_keeps_a_partial_file(self):
        job = self.job()
        job.cancel.set()
        self.manager.run(job)

        self.assertEqual(job.stage, self.settings.CANCELLED)
        self.assertFalse(self.imported)
        self.assertIn("resume", job.note)

    def test_a_cached_asset_imports_without_touching_the_network(self):
        first = self.job()
        self.manager.run(first)
        self.tasks.run_pending()

        def explode(*args, **kwargs):
            raise AssertionError("download_url must not be called for a cached asset")

        self.settings.fab.download_url = explode
        second = self.job()
        self.manager.run(second)
        self.assertEqual(second.stage, self.settings.IMPORTING)
        self.assertIn("already in your library", second.note)
        self.tasks.run_pending()
        self.assertEqual(second.stage, self.settings.DONE)
        self.assertEqual(self.imported, [first.uid, second.uid])

    def test_signing_out_turns_into_a_failed_job_not_an_exception(self):
        def refused(*args, **kwargs):
            raise self.errors.AuthRequired("no bundle")

        self.settings.fab.download_url = refused
        job = self.job()
        self.manager.run(job)
        self.assertEqual(job.stage, self.settings.FAILED)
        self.assertEqual(job.error, self.errors.AuthRequired.user_message)

    def test_corrupt_archive_is_retried_once_then_reported(self):
        RangeHandler.payload = b"PK\x03\x04 truncat"
        job = self.job()
        self.manager.run(job)
        self.assertEqual(job.stage, self.settings.FAILED)
        self.assertEqual(job.error, self.errors.ZipCorrupt.user_message)
        # Two attempts: the first failure was retried, not surfaced.
        self.assertEqual(len(RangeHandler.requests), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
