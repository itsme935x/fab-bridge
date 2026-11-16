"""Real-Blender end-to-end check for M3: download â†’ extract â†’ import.

    blender --factory-startup --background --python dev/download_smoke.py

Downloads the smallest texture-set file of one live Megascans listing, runs it
through `download.manager`, lets the main-thread import hook build the material,
then asserts the scene, the node graph and the index. Prints one JSON blob.

Anonymous download-info is used (there is no account in CI); the only thing
patched is `require_auth`.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import bpy  # noqa: E402

import fab_bridge  # noqa: E402
from fab_bridge.api import fab  # noqa: E402
from fab_bridge.api.query import Query  # noqa: E402
from fab_bridge.core import platform  # noqa: E402
from fab_bridge.download import archive, manager  # noqa: E402

_watchdog = threading.Timer(420.0, lambda: os._exit(2))
_watchdog.daemon = True
_watchdog.start()

report: dict = {"blender": bpy.app.version_string, "checks": {}}
LIB = tempfile.mkdtemp(prefix="fabbridge-smoke-")
report["library"] = LIB


def check(name: str, ok: bool, value=None) -> None:
    report["checks"][name] = {"ok": bool(ok), "value": value}


def wait_for(predicate, timeout: float = 60.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        platform.pump()
        if predicate():
            return True
        time.sleep(0.05)
    return False


try:
    bpy.context.preferences.system.use_online_access = True
    fab_bridge.register()

    # The smoke has no Epic session; the API accepts anonymous download-info.
    real_download_url = fab.download_url
    fab.download_url = lambda uid, code, file_uid, require_auth=True: real_download_url(
        uid, code, file_uid, require_auth=False
    )
    manager.library_root = lambda: LIB
    manager.keep_archives = lambda: False

    # -- importers that must be available ---------------------------------
    for namespace, name in (
        ("import_scene", "gltf"),
        ("import_scene", "fbx"),
        ("wm", "obj_import"),
        ("wm", "usd_import"),
    ):
        check(f"operator_{name}", hasattr(getattr(bpy.ops, namespace), name))

    # -- pick a small real surface ---------------------------------------
    page = fab.search(Query(q="asphalt"))
    listing = next(
        (item for item in page.listings if "texture-set" in item.format_codes), None
    )
    if listing is None:
        raise RuntimeError("no texture-set listing in the first page")
    check("listing", True, {"uid": listing.uid, "title": listing.title})

    files = fab.get_format_files(listing.uid, "texture-set")
    check("files", bool(files.files), [f.name for f in files.files])
    smallest = min(files.files, key=lambda entry: entry.size)
    quality = smallest.quality or "low"
    check("smallest", True, {"name": smallest.name, "mb": round(smallest.size / 1e6, 1)})

    # A clean scene: the default Cube is the active mesh and would otherwise
    # receive the material (which is the intended product behaviour).
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.view_layer.objects.active = None

    job = manager.Job(
        uid=listing.uid,
        title=listing.title,
        quality=quality,
        format_code="texture-set",
        listing=listing,
    )
    started = time.monotonic()
    manager.MANAGER.run(job)
    check("stage_after_download", job.stage, {"stage": job.stage, "error": job.error,
                                              "note": job.note})
    imported = wait_for(lambda: job.stage in manager.FINISHED, timeout=90)
    check("imported", imported, {"stage": job.stage, "error": job.error,
                                 "summary": job.summary})
    check("seconds", True, round(time.monotonic() - started, 1))
    check("extracted_files", bool(job.directory) and os.path.isdir(job.directory),
          job.directory)
    if os.path.isdir(job.directory):
        report["files"] = sorted(
            os.path.relpath(os.path.join(base, name), job.directory)
            for base, _dirs, names in os.walk(job.directory)
            for name in names
        )[:40]

    # -- material graph ---------------------------------------------------
    material = next((m for m in bpy.data.materials if m.name.startswith("M_")), None)
    check("material", material is not None, material.name if material else None)
    if material is not None:
        nodes = material.node_tree.nodes
        names = [node.name for node in nodes]
        check("nodes", all(
            name in names for name in ("Principled BSDF", "Image albedo", "Normal Map")
        ), names)
        principled = nodes.get("Principled BSDF")
        base = principled.inputs.get("Base Color") if principled else None
        normal = principled.inputs.get("Normal") if principled else None
        check("base_color_linked", bool(base and base.is_linked))
        check("normal_linked", bool(normal and normal.is_linked))
        check(
            "tex_images",
            True,
            len([node for node in nodes if node.type == "TEX_IMAGE"]),
        )
        if "Mapping" in names:
            check(
                "mapping_scale",
                True,
                [round(value, 3) for value in nodes["Mapping"].inputs["Scale"].default_value],
            )

    # -- scene ------------------------------------------------------------
    collections = [c.name for c in bpy.data.collections]
    collection = next((c for c in bpy.data.collections if c.name.startswith("FAB_")), None)
    check("collection", collection is not None, collections)
    if collection is not None:
        objects = list(collection.objects)
        check("objects", bool(objects), [o.name for o in objects])
        check(
            "material_on_object",
            any(o.data.materials for o in objects if o.type == "MESH"),
        )
        mesh = next((o for o in objects if o.type == "MESH"), None)
        if mesh is not None:
            width, height = archive.physical_size(job.metadata or {})
            dims = [round(v, 3) for v in mesh.dimensions]
            check(
                "plane_size",
                abs(dims[0] - width) < 0.05 and abs(dims[1] - height) < 0.05,
                {"expected": [width, height], "got": dims},
            )
            check("uv_layers", True, len(mesh.data.uv_layers))

    # -- index ------------------------------------------------------------
    index = manager.MANAGER.index(LIB)
    check(
        "index_row",
        index.lookup(job.uid, "texture-set", job.quality) == job.directory,
    )
    check(
        "index_cache",
        True,
        {"assets": index.downloaded_count(), "bytes": index.total_bytes()},
    )

    # -- a cache hit imports again without a network call ------------------
    again = manager.Job(
        uid=listing.uid, title=listing.title, quality=job.quality,
        format_code="texture-set", listing=listing,
    )
    manager.MANAGER.run(again)
    check("cache_hit", again.stage == manager.IMPORTING, again.stage)
    if wait_for(lambda: again.stage in manager.FINISHED, timeout=30):
        check("cache_import", again.stage == manager.DONE, again.summary)

    # -- a real 3D asset through the mesh importer ------------------------
    mesh_page = fab.search(Query(q="rock", listing_type="3d-model"))
    mesh_listing = next(
        (item for item in mesh_page.listings if "converted-files" in item.format_codes), None
    )
    if mesh_listing is None:
        check("mesh_listing", False, "no 3D listing with converted-files")
    else:
        mesh_files = fab.get_format_files(mesh_listing.uid, "converted-files")
        mesh_smallest = min(mesh_files.files, key=lambda entry: entry.size)
        check(
            "mesh_smallest",
            True,
            {"name": mesh_smallest.name, "mb": round(mesh_smallest.size / 1e6, 2)},
        )
        before = set(bpy.data.objects)
        mesh_job = manager.Job(
            uid=mesh_listing.uid,
            title=mesh_listing.title,
            quality=mesh_smallest.quality or "low",
            format_code="converted-files",
            listing=mesh_listing,
        )
        manager.MANAGER.run(mesh_job)
        wait_for(lambda: mesh_job.stage in manager.FINISHED, timeout=90)
        new_objects = [obj for obj in bpy.data.objects if obj not in before]
        check(
            "mesh_import",
            mesh_job.stage == manager.DONE,
            {"stage": mesh_job.stage, "error": mesh_job.error, "summary": mesh_job.summary},
        )
        found = archive.find_import_file(mesh_job.directory or "", "converted-files")
        check("mesh_file", True, os.path.basename(found) if found else None)
        check("mesh_objects", bool(new_objects), [obj.name for obj in new_objects][:10])
        check(
            "mesh_collection",
            True,
            [c.name for c in bpy.data.collections if c.name.startswith("FAB_")],
        )

except Exception:
    report["traceback"] = traceback.format_exc()
finally:
    try:
        fab_bridge.unregister()
    except Exception:
        report["unregister_traceback"] = traceback.format_exc()
    passed = all(entry["ok"] for entry in report["checks"].values())
    report["passed"] = passed
    print("FABBRIDGE_DOWNLOAD_SMOKE " + json.dumps(report, indent=1, default=str))
    shutil.rmtree(LIB, ignore_errors=True)
    _watchdog.cancel()
