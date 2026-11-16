"""Fab endpoint functions (SPEC.md §5.2).

Thin by design: build the request, hand back a parsed model. All policy
(retries, auth, rate limits) lives in transport.py.
"""

from __future__ import annotations

from ..core import errors, log
from . import endpoints as ep, transport
from .models import FormatFiles, Listing, SearchPage, User
from .query import Query, parse_facets

log = log.get("fab")


def get_me() -> User:
    """Session probe. A 401 here means signed out, not 'API changed'."""
    try:
        raw = transport.get_json(ep.USERS_ME, require_auth=True)
    except errors.AuthExpired:
        raise
    except errors.NotFound as exc:
        raise errors.ApiChanged("GET /users/me vanished") from exc
    return User.parse(raw)


def search(query: Query, *, cursor: str | None = None) -> SearchPage:
    raw = transport.get_json(ep.LISTINGS_SEARCH, params=query.params(cursor=cursor))
    page = SearchPage.parse(raw)
    if not page.listings and raw.get("results"):
        # Results existed but none parsed: the shape changed under us.
        raise errors.ApiChanged("listing results could not be parsed")
    return page


def facets(query: Query, facet: str = ep.FACET_LISTING_TYPE) -> dict[str, int]:
    """Bucket counts; also the only source of a result total (no `total` field)."""
    raw = transport.get_json(ep.LISTINGS_SEARCH, params=query.facet_params(facet))
    return parse_facets(raw, facet)


def get_listing(uid: str) -> Listing:
    raw = transport.get_json(ep.LISTING.format(uid=uid))
    listing = Listing.parse(raw)
    if listing is None:
        raise errors.ApiChanged(f"listing {uid} returned no uid")
    return listing


def get_format_files(uid: str, code: str) -> FormatFiles:
    raw = transport.get_json(ep.ASSET_FORMAT.format(uid=uid, code=code))
    parsed = FormatFiles.parse(raw)
    if not parsed.files and raw.get("files"):
        raise errors.ApiChanged(f"files for {code} could not be parsed")
    return parsed


def download_url(uid: str, code: str, file_uid: str, *, require_auth: bool = True) -> str:
    """Fresh signed CDN URL for one file (SPEC.md §5.5).

    The URL expires within minutes, so this is called immediately before every
    download — never cached in the index.
    """
    raw = transport.get_json(
        ep.DOWNLOAD_INFO.format(uid=uid, code=code, file_uid=file_uid),
        require_auth=require_auth,
    )
    for entry in raw.get("downloadInfo") or ():
        if isinstance(entry, dict) and entry.get("downloadUrl"):
            return str(entry["downloadUrl"])
    raise errors.ApiChanged(f"download-info for {file_uid} carried no URL")


def tag_groups() -> list[dict]:
    """Curated tag groups (style / theme / technical), used as filter chips."""
    raw = transport.get_json(ep.TAG_GROUPS)
    return [group for group in raw.get("results") or () if isinstance(group, dict)]


def taxonomy_categories() -> list[dict]:
    """All 407 categories, following cursors. Call sparingly; cached by the UI."""
    rows: list[dict] = []
    url = ep.TAXONOMY_CATEGORIES
    params = {"count": 100}
    seen: set[str] = set()
    for _ in range(40):
        raw = transport.get_json(url, params=params)
        rows.extend(row for row in raw.get("results") or () if isinstance(row, dict))
        cursor = (raw.get("cursors") or {}).get("next")
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
        params = {"count": 100, "cursor": cursor}
    return rows


def ping() -> bool:
    """Cheap anonymous reachability check used by diagnostics."""
    try:
        transport.get_json(ep.TAXONOMY_LISTING_TYPES)
        return True
    except errors.FabError as exc:
        log.debug("ping failed: %s", type(exc).__name__)
        return False
