"""Live smoke check against the real Fab API — not part of the unit suite.

    python fab_bridge/tests/live_check.py

Anonymous only: it must pass without an Epic account. It exercises the same
`Query`/`fab` code paths the panel uses, so a shape change fails here first.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fab_bridge.api import endpoints as ep, fab  # noqa: E402
from fab_bridge.api.query import Query  # noqa: E402
from fab_bridge.core import errors  # noqa: E402


def main() -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"{label} ... {'ok' if ok else 'FAIL'}{(' ' + detail) if detail else ''}")
        if not ok:
            failures += 1

    print("1. taxonomy reachable")
    check("   ping", fab.ping())

    print("2. search page (importable only, 24)")
    query = Query()
    page = fab.search(query)
    check("   results", len(page.listings) == ep.PAGE_SIZE, f"({len(page.listings)})")
    if page.listings:
        print("   first:", ", ".join(item.title for item in page.listings[:3]))

    print("3. cursor")
    check("   next cursor present", bool(page.cursor))

    print("4. facets")
    counts = fab.facets(query)
    total = sum(counts.values())
    check("   buckets", bool(counts), str(counts))
    check("   total plausible", abs(total - 22620) < 2000, f"(total={total})")

    print("5. listing detail")
    detail = fab.get_listing(page.listings[0].uid) if page.listings else None
    check("   parsed", detail is not None and bool(detail.title))
    if detail:
        check("   formats", bool(detail.formats), ",".join(detail.format_codes))
        check("   thumbnails", bool(detail.thumbnails), f"({len(detail.thumbnails)} sizes)")
        print("   thumb@320:", detail.thumb(320))

    print("6. asset-format files")
    if detail and detail.format_codes:
        code = "texture-set" if detail.has_format("texture-set") else detail.format_codes[0]
        files = fab.get_format_files(detail.uid, code)
        qualities = [entry.quality for entry in files.ordered() if entry.quality]
        check("   files", bool(files.files), f"{code}: {len(files.files)} file(s)")
        check("   parsed qualities", bool(qualities), ",".join(qualities))
        if files.files:
            print("   sizes:", ", ".join(f.size_label() for f in files.ordered()))

    print("7. tag groups (genre chips)")
    groups = fab.tag_groups()
    check("   groups", len(groups) >= 5, f"({len(groups)} groups)")
    print("   names:", ", ".join(str(g.get("name")) for g in groups[:6]))

    print("8. signed-out /users/me")
    try:
        fab.get_me()
    except (errors.AuthRequired, errors.AuthExpired):
        print("   ok (typed auth error)")
    except errors.FabError as exc:
        check("   typed error", False, type(exc).__name__)
    else:
        check("   typed error", False, "expected AuthRequired")

    # A stale category slug must surface as a typed error, not an empty grid.
    print("9. stale category slug is rejected")
    try:
        fab.search(Query(category="not-a-real-category"))
    except errors.ApiChanged:
        print("   ok (ApiChanged)")
    except errors.FabError as exc:
        check("   typed error", False, type(exc).__name__)
    else:
        check("   rejected", False, "expected a 400")

    print("\n" + ("all checks passed" if not failures else f"{failures} check(s) failed"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
