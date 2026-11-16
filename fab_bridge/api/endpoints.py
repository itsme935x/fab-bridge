"""Every URL and magic value in one place (SPEC.md §15).

If Fab moves something, this file is the whole blast radius.
"""

from __future__ import annotations

import os

# --- Fab API ---------------------------------------------------------------
FAB_BASE = os.environ.get("FAB_BRIDGE_BASE_URL", "https://www.fab.com").rstrip("/")
API = FAB_BASE + "/i"

LISTINGS_SEARCH = API + "/listings/search"
LISTING = API + "/listings/{uid}"
ASSET_FORMAT = API + "/listings/{uid}/asset-formats/{code}"
DOWNLOAD_INFO = API + "/listings/{uid}/asset-formats/{code}/files/{file_uid}/download-info"
ADD_TO_LIBRARY = API + "/listings/{uid}/add-to-library"
LIBRARY_SEARCH = API + "/library/search"
USERS_ME = API + "/users/me"

TAXONOMY_CATEGORIES = API + "/taxonomy/categories"
TAXONOMY_LISTING_TYPES = API + "/taxonomy/listing-types"
TAG_GROUPS = API + "/tags/groups"
TAG_AUTOCOMPLETE = API + "/tags/autocomplete"

# --- Epic OAuth ------------------------------------------------------------
OAUTH_HOST = "https://account-public-service-prod03.ol.epicgames.com"
OAUTH_TOKEN = OAUTH_HOST + "/account/api/oauth/token"
OAUTH_KILL = OAUTH_HOST + "/account/api/oauth/sessions/kill/{token}"
LOGIN_PAGE = "https://www.epicgames.com/id/login"
CODE_REDIRECT = "https://www.epicgames.com/id/api/redirect"

# Public clients shipped inside the launcher. See RESEARCH.md §2.1.
CLIENT_LAUNCHER = ("34a02cf8f4414e29b15921876da36f9a", "daafbccc737745039dffe53d94fc76cf")
CLIENT_DESKTOP = ("c85f4c6238b04d3399bc0080e83b83fc", None)

# --- Fixed API values (verified 2026-11-16, RESEARCH.md §3) ----------------
PAGE_SIZE = 24  # server hard cap; asking for 48 returns 24

QUIXEL_SELLER = "o-cbf5bbfdb340d6a839b5520cb31328"

# Only these sort keys behave as named. Anything else returns 200 with a
# different order, so the UI may not offer free-form values.
SORTS = (
    ("-relevance", "Relevance"),
    ("-publishedAt", "Newest"),
    ("publishedAt", "Oldest"),
    ("-firstPublishedAt", "Recently added"),
    ("-ratings.averageRating", "Top rated"),
)
SORT_VALUES = tuple(k for k, _ in SORTS)

LISTING_TYPES = ("material", "3d-model", "decal", "brush")

# Formats Blender can import. Sending an unknown code yields an empty result
# set rather than an error, so this list is the only source of truth.
IMPORTABLE_FORMATS = ("converted-files", "fbx", "gltf", "texture-set", "glb", "usd", "usdz", "obj", "blend")

# The subset that actually occurs on Megascans; used for the "importable only"
# toggle because these four cover 22,091 of 22,620 listings.
IMPORTABLE_FILTER = ("fbx", "gltf", "converted-files", "texture-set")

FACET_LISTING_TYPE = "listing_type"
FACET_CATEGORY = "category"
FACET_CATEGORY_PER_TYPE = "category_per_listing_type"

API_SCHEMA = 1
