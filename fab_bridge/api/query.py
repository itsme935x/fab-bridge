"""Search query → Fab parameters (SPEC.md §5.3).

Kept separate from the UI so the exact wire format is unit-testable: counts,
repeated parameters, the importable union, and the five validated sort keys.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import endpoints as ep


@dataclass(frozen=True)
class Query:
    q: str = ""
    listing_type: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    sort: str = "-relevance"
    importable: bool = True
    seller: str = ep.QUIXEL_SELLER

    def params(self, *, count: int = ep.PAGE_SIZE, cursor: str | None = None):
        """Ordered pairs, because `requests` must repeat, not comma-join."""
        params: list[tuple[str, str]] = []
        if self.seller:
            params.append(("sellers", self.seller))
        text = self.q.strip()
        if text:
            params.append(("q", text))
        # A stale/typed value must never reach the server: sort_by and
        # asset_formats are not validated upstream.
        if self.listing_type in ep.LISTING_TYPES:
            params.append(("listing_types", self.listing_type))
        if self.category:
            params.append(("categories", self.category))
        for tag in self.tags:
            if tag:
                params.append(("tags", tag))
        if self.sort in ep.SORT_VALUES:
            params.append(("sort_by", self.sort))
        if self.importable:
            params.extend(("asset_formats", code) for code in ep.IMPORTABLE_FILTER)
        params.append(("count", str(count)))
        if cursor:
            params.append(("cursor", cursor))
        return params

    def facet_params(self, facet: str = ep.FACET_LISTING_TYPE):
        """`count=0&aggregateOn=…` carries every other filter."""
        return self.params(count=0) + [("aggregateOn", facet)]

    def replaced(self, **changes) -> "Query":
        return replace(self, **changes)

    def is_filtered(self) -> bool:
        return bool(self.q.strip() or self.has_chips())

    def has_chips(self) -> bool:
        """Chip filters only; the text query has its own field."""
        return bool(self.listing_type or self.category or self.tags)

    def clear_filters(self) -> "Query":
        return replace(self, listing_type="", category="", tags=())

    def toggle_tag(self, slug: str) -> "Query":
        tags = list(self.tags)
        if slug in tags:
            tags.remove(slug)
        elif slug:
            tags.append(slug)
        return replace(self, tags=tuple(tags))


def facet_key(facet: str) -> str:
    """`listing_type` -> `listingType` in the aggregations payload."""
    return {
        ep.FACET_LISTING_TYPE: "listingType",
        ep.FACET_CATEGORY: "category",
        ep.FACET_CATEGORY_PER_TYPE: "categoryPerListingType",
        "channel": "channel",
    }.get(facet, facet)


def parse_facets(raw: dict, facet: str = ep.FACET_LISTING_TYPE) -> dict[str, int]:
    """`{bucket: docCount}` for one aggregation, tolerant of missing data."""
    aggregations = raw.get("aggregations")
    if not isinstance(aggregations, dict):
        return {}
    bucket = aggregations.get(facet_key(facet))
    if not isinstance(bucket, dict):
        return {}
    counts: dict[str, int] = {}
    for key, value in (bucket.get("buckets") or {}).items():
        if isinstance(value, dict):
            counts[str(key)] = int(value.get("docCount") or 0)
    return counts
