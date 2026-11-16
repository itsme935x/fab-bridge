"""Format/quality resolution (SPEC.md §7). Pure functions, unit-tested.

Quality is never a request parameter: Fab ships one file per tier and the tier
is encoded in the filename (`_4k`, `_high`, …). This module is the only place
that decides which of those files a user gets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..api.models import QUALITY_LABELS, QUALITY_RANK, AssetFile, FormatFiles, Listing
from ..core import errors

#: Preference chains (§7.1). `texture-set` is a material set, not a mesh, so it
#: is never mixed with the mesh chain.
MESH_CHAIN = ("converted-files", "blend", "glb", "fbx", "gltf", "usd", "usdz", "obj")
PLANT_CHAIN = ("fbx", "converted-files", "gltf", "glb", "usd", "obj")
SURFACE_CHAIN = ("texture-set",)


@dataclass(frozen=True)
class Plan:
    uid: str
    title: str
    format_code: str
    format_name: str
    file_uid: str
    filename: str
    size: int
    quality: Optional[str] = None
    note: str = ""

    @property
    def quality_label(self) -> str:
        return QUALITY_LABELS.get(self.quality or "", self.quality or "File")

    @property
    def size_label(self) -> str:
        if self.size >= 1_000_000_000:
            return f"{self.size / 1_000_000_000:.1f} GB"
        if self.size >= 1_000_000:
            return f"{self.size / 1_000_000:.0f} MB"
        return f"{max(1, self.size // 1000)} KB"

    def summary(self) -> str:
        return f"{self.format_name} · {self.quality_label} · {self.size_label}"


def is_plant(listing: Listing) -> bool:
    haystack = f"{listing.category_path} {listing.category_name} {' '.join(listing.tags)}".lower()
    return "nature-plants" in haystack or "3d plant" in haystack or "plant" in haystack.split()


def format_chain(listing: Listing) -> tuple[str, ...]:
    """Formats to try, best first, for this listing (§7.1)."""
    codes = set(listing.format_codes)
    if "texture-set" in codes:
        return SURFACE_CHAIN
    chain = PLANT_CHAIN if is_plant(listing) else MESH_CHAIN
    return tuple(code for code in chain if code in codes)


def pick_format(listing: Listing, wanted: str | None = None) -> str:
    """The user's pinned format if it exists, else the first chain entry."""
    codes = set(listing.format_codes)
    if wanted and wanted in codes:
        return wanted
    chain = format_chain(listing)
    if chain:
        return chain[0]
    # Fall back to anything Blender can read, even if it is not in our order.
    from ..api import endpoints as ep

    for code in ep.IMPORTABLE_FORMATS:
        if code in codes:
            return code
    raise errors.UnsupportedFormat(f"no importable format on {listing.uid}")


def resolve(
    listing: Listing,
    files: FormatFiles,
    wanted_quality: str = "high",
    wanted_format: str | None = None,
) -> Plan:
    """Choose one concrete file (SPEC.md §7.3).

    `files` must belong to the format that will be downloaded; callers get it
    from `api.fab.get_format_files`.
    """
    if not files.files:
        raise errors.UnsupportedFormat(f"{files.format.code} has no files")

    candidates = [entry for entry in files.ordered() if entry.quality]
    wanted_rank = QUALITY_RANK.get(wanted_quality, 2)
    note = ""

    if candidates:
        exact = next((entry for entry in candidates if entry.quality == wanted_quality), None)
        if exact is not None:
            chosen = exact
        else:
            # Nearest lower tier first, then nearest higher.
            lower = [e for e in candidates if QUALITY_RANK.get(e.quality, 99) <= wanted_rank]
            higher = [e for e in candidates if QUALITY_RANK.get(e.quality, 99) > wanted_rank]
            if lower:
                chosen = max(lower, key=lambda e: QUALITY_RANK.get(e.quality, 99))
            else:
                chosen = min(higher, key=lambda e: QUALITY_RANK.get(e.quality, 99))
            note = (
                f"{QUALITY_LABELS.get(wanted_quality, wanted_quality)} not available; "
                f"using {QUALITY_LABELS.get(chosen.quality, chosen.quality)}"
            )
    else:
        # Single-file formats (some decals, plants) carry no tier token.
        chosen = _single_file(files.files)
        note = "single file"

    return Plan(
        uid=listing.uid,
        title=listing.title,
        format_code=files.format.code,
        format_name=files.format.name or files.format.code,
        file_uid=chosen.uid,
        filename=chosen.name,
        size=chosen.size,
        quality=chosen.quality,
        note=note,
    )


def _single_file(files: tuple[AssetFile, ...]) -> AssetFile:
    """Largest file that is not an EXR master or a texture side-car."""
    usable = [entry for entry in files if entry.uid]
    if not usable:
        raise errors.UnsupportedFormat("no file entries")
    return max(usable, key=lambda entry: entry.size)
