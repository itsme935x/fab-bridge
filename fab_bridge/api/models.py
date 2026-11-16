"""Response models (SPEC.md §5.4).

Parsing rule: unknown keys are ignored, missing optional fields become None,
and one bad field never fails a whole page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

# §7.2 — quality is derived from the filename, never from a request parameter.
SURFACE_RESOLUTIONS = {"1k": "low", "2k": "mid", "4k": "high", "8k": "raw"}
QUALITY_LABELS = {"low": "Low", "mid": "Mid", "high": "High", "raw": "Raw"}
QUALITY_RANK = {"low": 0, "mid": 1, "high": 2, "raw": 3}

_SURFACE_Q = re.compile(r"_(1k|2k|4k|8k)(?=[._])", re.IGNORECASE)
_ASSET_Q = re.compile(r"_(low|mid|high|raw)(?=[._])", re.IGNORECASE)

#: Megascans tiers, spelled as users pick them. The tier name alone ("High")
#: never says 4K, which is exactly what a texture user is choosing.
RESOLUTION_LABELS = {"low": "1K", "mid": "2K", "high": "4K", "raw": "8K"}
_RES_IN_NAME = re.compile(r"_(1|2|4|8)k(?=[._])", re.IGNORECASE)


def parse_quality(filename: str) -> Optional[str]:
    """`..._4k.zip` -> high, `..._low.zip` -> low, anything else -> None."""
    if not filename:
        return None
    match = _SURFACE_Q.search(filename)
    if match:
        return SURFACE_RESOLUTIONS[match.group(1).lower()]
    match = _ASSET_Q.search(filename)
    if match:
        return match.group(1).lower()
    return None


def resolution_label(filename: str, quality: Optional[str] = None) -> str:
    """Texture resolution a file ships at: `2K`.

    Prefers the filename token (`Asphalt_2K_BaseColor.jpg`, `pack_4k.zip`) and
    otherwise uses the tier's documented resolution (`low`→1K … `raw`→8K).
    Empty when there is no tier to map either.
    """
    match = _RES_IN_NAME.search(filename or "")
    if match:
        return f"{match.group(1)}K"
    return RESOLUTION_LABELS.get(quality or "", "")


def _first(mapping: Any, *keys: str, default=None):
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) is not None:
            return mapping[key]
    return default


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _images(entries) -> tuple[tuple[int, str], ...]:
    """Flatten `[{images:[{url,width}], type}]` into unique (width, url)."""
    found: dict[str, int] = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        for image in entry.get("images") or ():
            if not isinstance(image, dict):
                continue
            url = image.get("url")
            width = _as_int(image.get("width"))
            if not url or not width:
                continue
            found[str(url)] = width
    return tuple(sorted(((width, url) for url, width in found.items()), key=lambda p: p[0]))


def pick_image(
    images: tuple[tuple[int, str], ...], limit: int
) -> Optional[tuple[int, str]]:
    """Largest image at or below `limit`; else the smallest available."""
    if not images:
        return None
    under = [pair for pair in images if pair[0] <= limit]
    if under:
        return max(under, key=lambda pair: pair[0])
    return min(images, key=lambda pair: pair[0])


@dataclass(frozen=True)
class User:
    uid: str
    display_name: str
    email: str = ""
    has_accepted_buyer_tos: bool = False

    @classmethod
    def parse(cls, raw: dict) -> "User":
        return cls(
            uid=str(_first(raw, "uid", "accountId", "account_id", default="")),
            display_name=str(
                _first(raw, "displayName", "display_name", "username", "name", default="")
            ),
            email=str(_first(raw, "email", default="")),
            has_accepted_buyer_tos=bool(_first(raw, "hasAcceptedBuyerTos", default=False)),
        )


@dataclass(frozen=True)
class License:
    name: str
    uid: str = ""
    price_tier: str = ""

    @classmethod
    def parse(cls, raw: dict) -> "License":
        return cls(
            name=str(_first(raw, "name", default="")),
            uid=str(_first(raw, "uid", default="")),
            price_tier=str(_first(raw, "priceTier", default="")),
        )


def parse_format(raw: Any) -> Optional["FormatInfo"]:
    """Handles both search shape (`assetFormatType`) and detail shape."""
    if not isinstance(raw, dict):
        return None
    inner = raw.get("assetFormatType")
    source = inner if isinstance(inner, dict) else raw
    code = source.get("code")
    if not code:
        return None
    extensions = source.get("extensions") or ()
    return FormatInfo(
        code=str(code),
        name=str(source.get("name") or code),
        extensions=tuple(str(ext) for ext in extensions if ext),
        group=str(source.get("groupName") or ""),
    )


@dataclass(frozen=True)
class FormatInfo:
    code: str
    name: str
    extensions: tuple[str, ...] = ()
    group: str = ""


@dataclass(frozen=True)
class AssetFile:
    uid: str
    name: str
    size: int = 0
    quality: Optional[str] = None
    file_type: Optional[str] = None
    vertex_count: Optional[int] = None
    face_count: Optional[int] = None
    file_url: Optional[str] = None

    @property
    def megabytes(self) -> float:
        return self.size / 1_000_000

    def size_label(self) -> str:
        # Decimal units, matching how Fab labels the same files.
        if self.size >= 1_000_000_000:
            return f"{self.size / 1_000_000_000:.1f} GB"
        if self.size >= 1_000_000:
            return f"{self.size / 1_000_000:.0f} MB"
        return f"{max(1, self.size // 1000)} KB"

    @classmethod
    def parse(cls, raw: dict) -> "AssetFile":
        name = str(_first(raw, "name", default=""))
        return cls(
            uid=str(_first(raw, "uid", default="")),
            name=name,
            size=_as_int(_first(raw, "size", default=0)) or 0,
            quality=parse_quality(name),
            file_type=_first(raw, "fileType", default=None),
            vertex_count=_as_int(_first(raw, "vertexCount", default=None)),
            face_count=_as_int(_first(raw, "faceCount", default=None)),
            file_url=_first(raw, "file", default=None),
        )


@dataclass(frozen=True)
class FormatFiles:
    format: FormatInfo
    files: tuple[AssetFile, ...] = ()
    tech: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.tech is None:
            object.__setattr__(self, "tech", {})

    def ordered(self) -> tuple[AssetFile, ...]:
        """Low → raw, then unparsable files by name."""
        return tuple(
            sorted(
                self.files,
                key=lambda f: (
                    QUALITY_RANK.get(f.quality, 99),
                    f.name,
                ),
            )
        )

    @classmethod
    def parse(cls, raw: dict) -> "FormatFiles":
        fmt = parse_format(raw) or FormatInfo(code="", name="")
        files = tuple(
            AssetFile.parse(item)
            for item in raw.get("files") or ()
            if isinstance(item, dict)
        )
        return cls(format=fmt, files=files, tech=raw.get("techDetails") or {})


@dataclass(frozen=True)
class Listing:
    uid: str
    title: str
    listing_type: str = ""
    seller: str = ""
    seller_id: str = ""
    is_free: bool = False
    claimable: bool = False
    category_name: str = ""
    category_path: str = ""
    tags: tuple[str, ...] = ()
    licenses: tuple[License, ...] = ()
    formats: tuple[FormatInfo, ...] = ()
    thumbnails: tuple[tuple[int, str], ...] = ()
    gallery: tuple[tuple[int, str], ...] = ()
    description_html: str = ""
    url: str = ""

    # --- convenience ------------------------------------------------------
    @property
    def format_codes(self) -> tuple[str, ...]:
        return tuple(fmt.code for fmt in self.formats)

    def has_format(self, code: str) -> bool:
        return code in self.format_codes

    def thumb(self, limit: int = 320) -> Optional[tuple[int, str]]:
        return pick_image(self.thumbnails, limit)

    def hero(self, limit: int = 1920) -> Optional[tuple[int, str]]:
        return pick_image(self.gallery, limit) or pick_image(self.thumbnails, limit)

    def format_label(self) -> str:
        """Short card caption: what Blender would get."""
        for code, label in (
            ("texture-set", "Texture Set"),
            ("converted-files", "glTF"),
            ("blend", "Blend"),
            ("fbx", "FBX"),
            ("gltf", "glTF"),
            ("usd", "USD"),
            ("obj", "OBJ"),
        ):
            if self.has_format(code):
                return label
        return "UE only"

    @classmethod
    def parse(cls, raw: Any) -> Optional["Listing"]:
        if not isinstance(raw, dict):
            return None
        uid = raw.get("uid")
        if not uid:
            return None

        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        category = raw.get("category") if isinstance(raw.get("category"), dict) else {}

        formats = tuple(
            fmt
            for fmt in (parse_format(item) for item in raw.get("assetFormats") or ())
            if fmt is not None
        )

        tags = tuple(
            str(tag.get("name") or tag.get("slug") or "")
            for tag in raw.get("tags") or ()
            if isinstance(tag, dict) and (tag.get("name") or tag.get("slug"))
        )

        licenses = tuple(
            License.parse(item)
            for item in raw.get("licenses") or ()
            if isinstance(item, dict)
        )

        # `medias` also carries the Sketchfab 3D viewer entry; keep stills only.
        gallery_sources = [
            media
            for media in raw.get("medias") or ()
            if isinstance(media, dict) and media.get("type") not in {"model", None}
        ]

        seller_id = str(_first(user, "sellerId", "uid", default=""))
        return cls(
            uid=str(uid),
            title=str(raw.get("title") or "Untitled"),
            listing_type=str(raw.get("listingType") or raw.get("productType") or ""),
            seller=str(_first(user, "sellerName", default="")),
            seller_id=seller_id,
            is_free=bool(raw.get("isFree") or False),
            claimable=bool(raw.get("isQuixelClaimable") or False),
            category_name=str(category.get("name") or ""),
            category_path=str(category.get("path") or ""),
            tags=tags,
            licenses=licenses,
            formats=formats,
            thumbnails=_images(raw.get("thumbnails")),
            gallery=_images(gallery_sources),
            description_html=str(raw.get("description") or ""),
            url=f"https://www.fab.com/listings/{uid}",
        )


@dataclass(frozen=True)
class SearchPage:
    listings: tuple[Listing, ...] = ()
    cursor: Optional[str] = None

    @classmethod
    def parse(cls, raw: dict) -> "SearchPage":
        listings = tuple(
            listing
            for listing in (Listing.parse(item) for item in raw.get("results") or ())
            if listing is not None
        )
        cursors = raw.get("cursors") if isinstance(raw.get("cursors"), dict) else {}
        cursor = cursors.get("next") or None
        return cls(listings=listings, cursor=str(cursor) if cursor else None)


@dataclass(frozen=True)
class LibraryItem:
    uid: str
    title: str
    listing_type: str = ""
    owned: bool = True
    formats: tuple[FormatInfo, ...] = ()
    synced_at: float = 0.0
