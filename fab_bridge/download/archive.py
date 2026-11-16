"""ZIP handling: safe extraction, file discovery, Megascans metadata.

Pure stdlib so it can be tested without Blender. `safe_extract` refuses any
entry that would land outside the destination — zips from the internet are
untrusted input.
"""

from __future__ import annotations

import json
import os
import re
import zipfile

from ..core import errors

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff", ".tga"}
MESH_EXTENSIONS = {
    "converted-files": (".glb", ".gltf", ".fbx", ".usdz", ".usdc", ".usda", ".usd", ".obj"),
    "gltf": (".glb", ".gltf"),
    "fbx": (".fbx",),
    "obj": (".obj",),
    "usd": (".usdz", ".usdc", ".usda", ".usd"),
    "blend": (".blend",),
}

#: Filename token → material channel (§10.2). Order matters: the first match
#: wins, so more specific names come first.
CHANNEL_TOKENS = (
    ("normal", ("normal", "nor", "nrm")),
    ("albedo", ("albedo", "basecolor", "base_color", "diffuse", "diff", "col", "color")),
    ("roughness", ("roughness", "rough")),
    ("gloss", ("gloss", "glossiness")),
    ("metal", ("metalness", "metallic", "metal")),
    ("ao", ("ao", "ambientocclusion", "occlusion")),
    ("displacement", ("displacement", "disp", "height")),
    ("bump", ("bump",)),
    ("opacity", ("opacity", "alpha", "mask")),
    ("specular", ("specular", "spec")),
    ("translucency", ("translucency", "translucent")),
    ("emission", ("emission", "emissive")),
    ("cavity", ("cavity",)),
)

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESOLUTION = re.compile(r"(\d+)k", re.IGNORECASE)
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_name(text: str, fallback: str = "asset", limit: int = 96) -> str:
    """A filename that is legal on Windows, macOS and Linux."""
    cleaned = _UNSAFE.sub("", str(text or "")).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = fallback
    cleaned = cleaned[:limit].rstrip(" .") or fallback
    # Windows refuses these even with an extension (`NUL.zip` is still NUL).
    if cleaned.split(".")[0].lower() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def safe_extract(zip_path: str, dest: str) -> list[str]:
    """Extract every regular file, rejecting absolute/`..` paths and links."""
    root = os.path.realpath(dest)
    written: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            if name.startswith("__MACOSX/") or "/__MACOSX/" in name:
                continue
            # Reject symlinks outright: they are never part of an asset pack.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise errors.ZipCorrupt(f"symlink in archive: {name}")
            target = os.path.realpath(os.path.join(root, name))
            if target != root and not target.startswith(root + os.sep):
                raise errors.ZipCorrupt(f"path escapes the archive: {name}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as out:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)
            written.append(target)
    return written


def verify_zip(path: str) -> None:
    """Cheap integrity check: the central directory must parse.

    A full `testzip()` re-decompresses every byte (minutes on a 280 MB pack),
    and a truncated download already fails at this point.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise errors.ZipCorrupt(str(exc)) from exc
    if not names:
        raise errors.ZipCorrupt("archive is empty")


def _walk_files(directory: str):
    for base, _dirs, files in os.walk(directory):
        for name in files:
            yield os.path.join(base, name)


def find_import_file(directory: str, format_code: str) -> str | None:
    """The file Blender should open for a mesh format, or None for textures."""
    wanted = MESH_EXTENSIONS.get(format_code)
    if not wanted:
        return None
    found: list[tuple[int, str, str]] = []
    for path in _walk_files(directory):
        extension = os.path.splitext(path)[1].lower()
        if extension not in wanted:
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        found.append((wanted.index(extension), -size, path))
    if not found:
        return None
    found.sort()
    return found[0][2]


def find_textures(directory: str) -> dict[str, str]:
    """Channel → image path, preferring the highest resolution available."""
    best: dict[str, tuple[int, int, str]] = {}
    for path in _walk_files(directory):
        stem, extension = os.path.splitext(os.path.basename(path))
        if extension.lower() not in IMAGE_EXTENSIONS:
            continue
        channel = channel_of(stem)
        if not channel:
            continue
        resolution = _resolution(stem)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        rank = (resolution, size)
        if channel not in best or rank > best[channel][:2]:
            best[channel] = (resolution, size, path)
    return {channel: value[2] for channel, value in best.items()}


def channel_of(stem: str) -> str | None:
    parts = re.split(r"[^a-z0-9]+", stem.lower())
    for channel, tokens in CHANNEL_TOKENS:
        for token in tokens:
            if any(part == token or part.startswith(token) for part in parts if part):
                return channel
    return None


def _resolution(stem: str) -> int:
    match = _RESOLUTION.search(stem)
    return int(match.group(1)) if match else 0


def read_metadata(directory: str) -> dict:
    """Megascans ships a small JSON with displacement scale and physical size."""
    for path in sorted(_walk_files(directory)):
        if not path.lower().endswith(".json"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and any(
            key in payload for key in ("displacementScale", "physicalSize", "semanticTags")
        ):
            return payload
    return {}


def physical_size(metadata: dict, default: float = 2.0) -> tuple[float, float]:
    """`"2x2"` / `"4x2"` → metres, so a preview plane is the right size."""
    raw = str(metadata.get("physicalSize") or "").lower().replace(" ", "")
    match = re.match(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$", raw)
    if not match:
        return default, default
    return float(match.group(1)), float(match.group(2))
