# Fab Bridge — technical specification

**Status:** design spec; 0.1.0 implements M1 (auth) + M2 (browse) + M3 (download/import) as described here, with the deviations listed in §16. My Library (§6.5/§9 library table) is designed but not shipped.
**Derived from:** `RESEARCH.md` (endpoints verified live 2026-11-16).
**Decisions:** Megascans catalogue only in 0.1.0 · Blender 4.2 LTS–5.x extension · Epic copy-code login page with opportunistic localhost capture · stills-only previews.

---

## 1. Goals and non-goals

### Goals
1. Sign in with an Epic account from inside Blender, once, and stay signed in.
2. Browse the Quixel Megascans catalogue (surfaces, 3D props, plants, decals), search and filter it.
3. Browse the user's Fab library (anything claimed/owned) with an honest "can Blender use this?" state. *(Designed in §6.5; **not in 0.1.0** — needs a live account to validate, so the tab is deferred rather than shipped unverified.)*
4. Pick format + quality, download with progress/cancel/resume, extract, and cache on disk.
5. Import into Blender with correct materials, tiling, displacement, normals, plants and LODs.
6. Never require the Epic Games Launcher, Unreal Engine, or the old Quixel Bridge.
7. Degrade gracefully: rate limits, expired tokens, changed API, missing formats, disk full.

### Non-goals (v1)
- Importing `.uasset` / UE packs, UEFN, Unity packages.
- Buying assets, carts, checkout, license management.
- Redistributing assets or bundling any asset data with the addon.
- Headless/CI bulk harvesting of the catalogue.
- Modifying or "repairing" a user's Fab library beyond claiming free items they explicitly ask for.
- 3D / turntable previews. Stills only; `Open on Fab` covers the rest (§12.5).

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Listing** | A Fab product page (`uid`), e.g. one Megascans surface. |
| **Format** | `assetFormatType.code`: `texture-set`, `fbx`, `gltf`, `converted-files`, `additional-files`, `glb`, `usd`, `unreal-engine`… |
| **File** | One downloadable ZIP inside a format (`files[].uid`), e.g. `asphalt_road_..._4k.zip`. |
| **Quality** | `low / mid / high / raw`, encoded in the file name (`_1k/_2k/_4k/_8k` or `_low/_mid/_high/_raw`). |
| **Tier** | The concrete quality of a specific file after resolution. |
| **Claim** | `add-to-library` for a free listing. |
| **Job** | One download+import unit of work in the queue. |
| **Library** | Fab "My Library", addressed via `/i/library/search`. |

---

## 3. Platform & packaging

```
fab_bridge/
  blender_manifest.toml
  __init__.py
  importer.py            # bpy import + Megascans material builder (main thread only)
  api/                   # endpoints, transport, models, query, fab client
  auth/                  # oauth, token store, loopback listener, manager
  core/                  # errors, log, platform (timer/tick), tasks
  download/              # resolver, manager (queue/stream), archive (zip/maps), store (SQLite)
  ui/                    # panels, operators, browser state, previews, prefs
  tests/                 # unit + stub bpy (excluded from the shipped zip)
```

### 3.1 `blender_manifest.toml`

```toml
schema_version = "1.0.0"
id = "fab_bridge"
version = "0.1.0"
name = "Fab Bridge"
tagline = "Browse and import Fab / Quixel Megascans assets"
maintainer = "Fab Bridge contributors"
type = "add-on"
license = ["SPDX:GPL-3.0-or-later"]
blender_version_min = "4.2.0"
tags = ["Import-Export", "Material"]

[permissions]
network = "Sign in to Fab and download assets you own"
files = "Save, extract and cache downloaded assets"
clipboard = "Read the Epic sign-in code from the clipboard"
```

- No `wheels/*`: Blender ships `requests`, `urllib3`, `certifi`, `numpy`, `sqlite3`.
- `license` must be GPL-3.0-or-later for add-ons on extensions.blender.org. `extension validate` does **not** enforce this; the upload page does (hit live 2026-11-16). The full text ships as `LICENSE`.
- `__init__.py` exposes `register()` / `unregister()`; there is no `bl_info` (the extension manifest replaces it).
- Hard requirement at enable time: `bpy.app.online_access` must be `True`. If not, show a blocking banner with an "Open Network Preferences" button (`bpy.ops.screen.userpref_show(section='SYSTEM')`), never raise in `register()`. Verified 2026-11-16: a fresh profile (`--factory-startup`, empty `BLENDER_USER_CONFIG`) defaults to **off**, so the banner is the first thing many users see — it must be actionable, not just informative.

### 3.2 Compatibility

| Blender | Status |
|---|---|
| 4.2 LTS | Primary target |
| 4.3–4.5 | Supported |
| 5.x | Supported (current user installs 5.2–5.4) |

Version-sensitive code paths (documented, each isolated):
- `material.displacement_method = 'BOTH'` (4.x) vs `material.cycles.displacement_method`.
- `bpy.ops.import_scene.obj` vs `bpy.ops.wm.obj_import`.
- Principled input names: `Specular IOR Level` (4.0+) vs `Specular`; `Transmission Weight` vs `Transmission`; `Sheen Weight`, `Coat Weight`.

---

## 4. Architecture

### 4.1 Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `core/errors.py` | Error taxonomy (§11) | — |
| `core/log.py` | Logging + redaction filter | stdlib |
| `core/platform.py` | bpy access, redraw, main-thread pump, tick registry | `bpy` |
| `core/tasks.py` | Worker → main-thread hand-off, `spawn()` for daemon jobs | `core.platform` |
| `auth/oauth.py` | Login URL, clipboard code extraction, token exchange/refresh/kill | `requests`, `auth.store` |
| `auth/flow_pkce.py` | Loopback PKCE flow (experimental flag) — not built yet | `api.transport`, `auth.store` |
| `auth/models.py` | `TokenBundle`, OAuth payload parsing | stdlib |
| `auth/store.py` | Token persistence (DPAPI / Keychain / 0600), refresh window | stdlib, `ctypes` |
| `auth/manager.py` | Session singleton, single-flight refresh, listeners | `auth.oauth`, `auth.store` |
| `api/transport.py` | HTTP session, auth header, retries, 429 backoff, timeouts, redaction, streaming GET | `requests`, `certifi` |
| `api/endpoints.py` | Every URL and verified magic value (§15) | — |
| `api/query.py` | `Query` → wire parameters, facet parsing (§5.3) | `api.endpoints` |
| `api/fab.py` | Endpoint functions, pagination, taxonomy/tags | `api.transport` |
| `api/models.py` | Dataclasses + parsers (tolerant to unknown fields) | stdlib |
| `download/resolve.py` | Format/quality resolution (§7) | `api.models` |
| `download/manager.py` | Job queue, worker threads, progress, cancel, resume | `download/fetch.py` |
| `download/fetch.py` | Streaming GET, Range resume, `.part`, retry | `api.transport` |
| `download/extract.py` | Zip validation, safe extraction, metadata parse | `zipfile`, `json` |
| `download/store.py` | SQLite index, thumbnails, disk accounting | `sqlite3` |
| `importers/dispatch.py` | Extension → Blender importer, main-thread only | `bpy` |
| `importers/materials.py` | Megascans node graph builder | `bpy` |
| `importers/plants.py` | Opacity/translucency/LOD/variation | `bpy`, `materials` |
| `importers/assets.py` | Collection, parenting, asset-mark, catalogs | `bpy` |
| `ui/panel.py` | Sidebar layout: account, search, filters, grid, detail | `bpy` |
| `ui/browser.py` | Search state, generation gating, pagination, selection | `api.fab`, `core.tasks` |
| `ui/previews.py` | Thumbnail/hero cache, `pcoll` lifecycle | `bpy`, `core.tasks` |
| `ui/props.py` | Scene properties (the search box) | `bpy` |
| `ui/library.py` | Library tab + sync | `api.fab`, `download.store` |
| `ui/queue.py` | Queue/progress/log panel | `download.manager` |
| `ui/ops.py` | Operators (login, search, select, claim, download, import, cancel) | everything |
| `ui/prefs.py` | Addon preferences | `bpy` |

### 4.2 Threading model

```
main thread (bpy)                     worker threads
─────────────────                     ──────────────
UI events, operators                  HTTP: search, detail, library
  │                                     download: streaming, extract
  ├─ enqueue Job ─────────────────────► job queue (queue.Queue)
  │
  ├─ bpy.app.timers(0.1s) ◄──────────── events queue (queue.Queue)
  │    • drain events → update UI state
  │    • pop 1 ready import → run bpy.ops.import_scene.*
  │    • load thumbnails (previews collection)
  ▼
```

Rules:
1. **Workers never touch `bpy`.** They emit `Event` objects (`Progress`, `StageChanged`, `Failed`, `Done`) onto a queue.
2. **All `bpy` work happens in the timer callback.** Imports are executed one per tick to keep the UI responsive; a job's stage becomes `importing` before the tick that performs it.
3. Threads are daemons; `unregister()` sets a stop event, cancels pending futures and unregisters the timer. In-flight downloads keep `.part` files for resume.
4. Concurrency: `max_workers = 3` for downloads (spec default; exposed in prefs 1–4). API mutations (claim) use a **single** dedicated worker at ≥300 ms spacing.
5. Search requests are cancellable by generation counter: a newer query bumps the generation; stale responses are dropped, not applied.

### 4.3 Data flow (happy path)

```
Search → select card → GET listing detail (formats+files)
       → resolve(plan) → POST/GET download-info → stream ZIP → verify
       → extract → parse asset JSON → import (main thread) → index row → card shows "Imported"
```

---

## 5. HTTP contract

Base `https://www.fab.com/i` (configurable constant, see §15 diagnostics).
Auth: `Authorization: Bearer <access_token>` on everything except public browse calls (which the client still sends it on, to keep behaviour identical to the web app).

### 5.1 Common request headers

```
User-Agent: FabBridge/<addon_ver> (Blender <blender_ver>; <platform>)
Accept: application/json
X-Requested-With: XMLHttpRequest
```

`requests.Session` is reused per account; `Accept-Encoding: gzip, deflate` left to requests.

### 5.2 Endpoints

| # | Method | Path | Auth | Purpose |
|---|---|---|---|---|
| E1 | GET | `/i/listings/search` | optional | Browse/search — see §5.3 for the verified parameter set |
| E1b | GET | `/i/listings/search?count=0&aggregateOn=…` | optional | Facets only: `category`, `listing_type`, `category_per_listing_type`, `channel` buckets with `docCount` |
| E2 | GET | `/i/listings/{uid}` | optional | Full detail: `title`, `description`, `licenses[]`, `thumbnails[]`, `medias[]`, `category`, `tags`, `assetFormats[]`, `techDetails` |
| E3 | GET | `/i/listings/{uid}/asset-formats/{code}` | optional | File list + specs for one format |
| E4 | GET | `/i/listings/{uid}/asset-formats/{code}/files/{fileUid}/download-info?platform=Windows` | optional¹ | Signed ZIP URL |
| E5 | POST | `/i/listings/{uid}/add-to-library` | **yes** | Claim (form-encoded `offer_id`) |
| E6 | GET | `/i/library/search?count=24&cursor=&sort_by=-createdAt` | **yes** | Library pages |
| E7 | GET | `/i/users/me` | **yes** | Session probe (`uid`, `hasAcceptedBuyerTos`, display name) |
| E8 | GET | `/i/taxonomy/categories` | optional | 407 categories, paginated: `{name, path, slug, uid}` |
| E9 | GET | `/i/taxonomy/listing-types` | optional | `material`, `3d-model`, `decal`, `brush`, … |
| E10 | GET | `/i/tags/groups` | optional | Tag groups: Rendering Style, Period/Theme, Animation, Functionality, Materials |
| E11 | GET | `/i/tags/autocomplete?q=` | optional | Tag suggestions |

¹ Verified to work anonymously for free listings. The client **always** sends the bearer token when signed in, and refuses to download when signed out (see §6.6).

### 5.3 Search, filters and taxonomy

All of the following verified live 2026-11-16 against the Quixel seller (`o-cbf5bbfdb340d6a839b5520cb31328`, 22,620 listings).

**Query parameters.** Repeatable params are repeated, not comma-joined.

| Param | Form | Notes |
|---|---|---|
| `q` | text | Free text. `q=rock` → 4,461 |
| `sellers` | seller uid | Locked to Quixel on the Megascans tab |
| `listing_types` | code | `material`, `3d-model`, `decal`, `brush`. Repeatable |
| `categories` | slug | `/` becomes `--`: `nature-plants--rocks-stones`. Repeatable |
| `tags` | slug | Repeatable. `tags=rock` → 2,868 |
| `asset_formats` | format code | Repeatable. `fbx`/`gltf`/`converted-files`/`texture-set` |
| `sort_by` | enum | `-relevance` (default), `-publishedAt`, `publishedAt`, `-firstPublishedAt`, `-ratings.averageRating` |
| `count` | int | Server caps at **24**; `48` returns 24 |
| `cursor` | opaque | From `cursors.next` |
| `is_free` | 1 | Accepted but a no-op — every Megascans listing is free |

**Two traps, both measured:**

1. **`sort_by` is not validated.** `-averageRating`, `-createdAt`, and bare `relevance` return HTTP 200 with a silently different order. Only the five values above may be offered in the UI.
2. **`asset_formats` is not validated either.** An unknown code returns 200 with zero results, not an error. Never send a code we did not read from the API.

`categories` and `tags` *are* validated, and the 400 body names the field:
`{"detail":{"categories":["Select a valid choice. <x> is not one of the available choices."]}}` — so a stale slug surfaces as `ApiChanged`, not a silent empty grid.

**Facets.** `count=0&aggregateOn=<facet>` returns `aggregations` (null otherwise) with `{docCount}` per bucket:

```
GET /i/listings/search?sellers=<quixel>&count=0&aggregateOn=listing_type
→ {"listingType":{"buckets":{"material":{"docCount":11053},
                             "3d-model":{"docCount":8825},
                             "decal":{"docCount":2223},
                             "brush":{"docCount":519}}}}
```

Facets respect the other filters (`q=rock` shrinks the buckets), which is what makes a filter panel with live counts possible. `aggregateOn=category` returns **uids**, so it is joined against the cached taxonomy.

**There is no `total` field.** `results` has only `aggregations, cursors, next, previous, results`. The result count shown in the UI comes from the facet call, which we already make per filter change — one extra 24-byte request per keystroke batch, not per card.

**Blender-importability filter.** Measured union of `asset_formats=fbx|gltf|converted-files|texture-set` → **22,091** of 22,620, broken down as material 11,053 (all), 3d-model 8,815 (of 8,825), decal 2,223 (all), brush **0**. So:

- every surface and decal has `texture-set`,
- exactly 10 3D models are UE-only,
- brushes have no Blender-usable format at all and must be hidden or greyed.

This makes the "only show importable" toggle a server-side filter rather than post-filtering a page we already paid for.

**Taxonomy caching.** `/i/taxonomy/categories` is 407 rows over 17 pages — fetched once, stored as JSON with a 7-day TTL, and used to render the category tree and to label `aggregateOn=category` buckets. `/i/tags/groups` gives the six curated groups (Rendering Style: anime/cartoon/stylized/voxel/lowpoly/realistic/pixel; Period/Theme: steampunk…scifi; plus Animation, Functionality, Materials). These are the "genre" chips — Fab has no genre field, the curated tag groups are the closest thing.

### 5.4 Normalised response models

```python
@dataclass(frozen=True)
class License:
    name: str; slug: str; offer_id: str | None; price: float | None

@dataclass(frozen=True)
class FormatInfo:
    code: str; name: str; extensions: tuple[str, ...]

@dataclass(frozen=True)
class AssetFile:
    uid: str; name: str; size: int
    quality: str | None          # low|mid|high|raw  (None when unparsable)
    file_type: str | None        # source|generated
    vertex_count: int | None
    face_count: int | None
    file_url: str | None         # only for converted-files (private S3, not usable directly)

@dataclass(frozen=True)
class FormatFiles:
    format: FormatInfo
    files: tuple[AssetFile, ...]
    tech: dict                   # raw techDetails, tolerantly preserved

@dataclass(frozen=True)
class Listing:
    uid: str; title: str; seller: str; listing_type: str
    is_free: bool; is_quixel: bool
    category_path: str
    tags: tuple[str, ...]
    licenses: tuple[License, ...]
    formats: tuple[FormatInfo, ...]
    thumbnails: tuple[tuple[int, str], ...]     # (width, url), ascending
    description_html: str
    tech: dict
    url: str

@dataclass(frozen=True)
class LibraryItem:
    uid: str; title: str; listing_type: str
    owned: bool = True
    formats: tuple[FormatInfo, ...] = ()
    synced_at: float = 0.0
```

Parsing rules: unknown keys ignored; missing optional fields → `None`; never let one bad field fail the whole page. **No field is trusted to exist** (`if raw.get("thumbnails") or ()`).

### 5.5 Pagination

- Fixed page size 24 (server caps it).
- Loop: `GET url` → read `cursors.next` / `next` → repeat. Opaque string; never construct cursors.
- Guard against loops: remember seen cursors, stop after 500 pages with a diagnostic.
- Search requests are generation-gated in the UI (§4.2 rule 5).

### 5.6 Retry / rate-limit policy (all calls)

| Condition | Behaviour |
|---|---|
| 429 | sleep `Retry-After` (fallback `30s · 2^n`, cap 10 min), max 6 attempts, then `RateLimited` error with "try again in X" |
| 5xx / connection error | retry 3× with 0.5 s, 1.5 s, 4 s + 0–300 ms jitter |
| 401 | single refresh + retry; second 401 → `AuthExpired` (sign out, prompt) |
| 403 | for `download-info`/CDN: re-resolve URL once; then `Forbidden` |
| 404 | `NotFound` (stale uid) — refresh the card, don't retry |
| Timeout | connect 10 s, read 30 s (API), 60 s (CDN, per chunk read) |

Rate budget (client-side token buckets): browse 2 req/s (burst 4), library 1 req/s, claim 1 per 320 ms, download-info 2 req/s, CDN 3 concurrent streams.

---

## 6. Authentication

### 6.1 Token bundle

```python
@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: float          # epoch seconds (from expires_at/expires_in)
    refresh_expires_at: float
    account_id: str
    display_name: str
    client_id: str
```

Response parsing (`auth.models.parse_oauth_response`) — Epic mixes units, so both are accepted:

| Target | Preferred field | Fallback |
|---|---|---|
| `expires_at` | `expires_in` (seconds, relative) | `expires_at` (ISO-8601 **or** epoch) |
| `refresh_expires_at` | `refresh_expires` (seconds) — *not* `refresh_expires_in` | `refresh_expires_at` (ISO-8601 **or** epoch) |

An ISO string must never reach `float()`: that was a real sign-in blocker (`ValueError` on `refresh_expires_at`, found in the field log 2026-11-16). Any parse failure is re-raised as `ApiChanged` with the exception type in `detail`, never leaked raw.

Token endpoint status mapping: `400` and `401` → `AuthRequired` ("That sign-in code was already used or has expired. Sign in again."), **not** `ApiChanged` — the only variable in that request is the credential.

### 6.2 Flow A — authorization code (default)

1. Build URL:
   `https://www.epicgames.com/id/login?redirectUrl=<urlencoded https://www.epicgames.com/id/api/redirect?clientId=<id>&responseType=code>`
2. `webbrowser.open(url)`.
3. If `auto_capture` is on, start the `127.0.0.1:443` listener. A direct
   `redirect_uri=https://localhost/launcher/authorized` is **not** sent: the
   account portal moved to `accounts.epicgames.com`, drops that unregistered
   redirect, and leaves a signed-in browser on account settings (seen live
   2026-11-16). Automatic capture rides on the `redirectUrl` link that the JSON
   page shows instead.
4. Modal operator polls up to **10 min**, 0.5 s interval — listener first, then
   clipboard, so either route can finish:
   - accept JSON containing `authorizationCode`, or a bare `[A-Za-z0-9]{16,64}` token;
   - stop watching on success, cancel, or timeout;
   - never log the code.
5. Exchange:
   `POST https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token`
   Basic `base64(client_id:client_secret)`, form: `grant_type=authorization_code&code=…&token_type=eg1`.
6. Persist bundle; close modal; refresh UI.

**What the user should see (documented by other launcher clients; not yet observed in a live sign-in here).** `launcherAppClient2` has no friendly consent page: after signing in, Epic renders a bare JSON viewer —

```json
{
  "warning": "Do not share this code with any 3rd party service. It allows full access to your Epic account.",
  "redirectUrl": "https://localhost/launcher/authorized?code=<code>",
  "authorizationCode": "<code>",
  "exchangeCode": null,
  "sid": null
}
```

That looks broken to anyone who expected a "return to Blender" page, so the UI must say so and the parser must take whatever the user copies (page text, the `redirectUrl`, or the bare code). The exact `redirectUrl` value is Epic's to choose; the parser accepts any `?code=` URL plus the two JSON fields. Implementation:

- Panel, signed out: "Epic ends on a JSON page. / Ctrl+A then Ctrl+C copies it."
- While the modal runs, `ui.ops.LOGIN["active"]` drives a short checklist in the Account box (plus **Cancel** and **Copy only**, or **Paste instead** when `auto_capture` is off), because a modal operator cannot draw its own UI.
- With capture on, the checklist says to click the redirect link or copy the page; the certificate warning for `https://localhost` is expected.
- `fab_bridge.login_paste` accepts the whole JSON blob, not just the code.
- `extract_code()` tries `"authorizationCode"` → `"code"` → `?code=` → bare token, in that order (a JWT is rejected).

Client: `launcherAppClient2` / `34a02cf8f4414e29b15921876da36f9a` / `daafbccc737745039dffe53d94fc76cf`.

### 6.3 Flow B — PKCE loopback (experimental, preference-gated)

```
GET https://www.epicgames.com/id/login
    ?client_id=c85f4c6238b04d3399bc0080e83b83fc
    &response_type=code
    &redirect_uri=http://127.0.0.1:<ephemeral>/launcher/authorized
    &scopes=basic_profile friends_list country presence
    &code_challenge=<S256(verifier)>&code_challenge_method=S256
    &state=<random>&prompt=select_account
```
- `http.server.ThreadingHTTPServer` bound to `127.0.0.1:0`; accept only `GET /launcher/authorized`; compare `state`; reply with a minimal "You can close this tab" page; 5 min timeout.
- Exchange with `token_type=eas` (client `egs-desktop`), then the documented `exchange_code` grant to obtain an EG1 token.
- **Gate:** hidden behind `prefs.experimental_pkce = False`. Enable only after a spike proves Epic accepts the loopback redirect; DPoP is optional server-side, but a `use_dpop_nonce` challenge must be handled as "flow unavailable, fall back to Flow A".

### 6.4 Storage

| OS | Mechanism | Path |
|---|---|---|
| Windows | DPAPI (`CryptProtectData`, `CRYPTPROTECT_UI_FORBIDDEN`) via `ctypes` | `user_resource('CONFIG')/fab_bridge/session.bin` |
| macOS | Keychain (`security add-generic-password -U -s fab_bridge -a <account> -w <json>`) | — |
| Linux | `secret-tool` if available, else file `0600` in `~/.config/fab_bridge/` | — |

Stored payload is the whole bundle as JSON. If the stored `client_id` differs from the current one, discard.

### 6.5 Refresh

- Refresh when `now > expires_at - 600`.
- Single-flight: `threading.Lock`; concurrent callers wait for the same result.
- On refresh failure (`invalid_grant`, `TOKEN_NOT_FOUND`, expired refresh): clear store, emit `AuthExpired`.
- Never refresh pre-emptively more than once per 60 s; jitter the refresh moment ±30 s.

### 6.6 Signed-out behaviour

| Action | Signed out |
|---|---|
| Browse/search | allowed (public API) |
| View listing detail/formats | allowed |
| Download/import | **blocked** — panel shows "Sign in to download" |
| Library tab | shows sign-in prompt |
| Claim | blocked |

Rationale: the free-listing endpoints are currently open, but the product decision is account-scoped usage; also needed for library, claims, and future entitlement enforcement.

### 6.7 Sign out

1. `DELETE https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/sessions/kill/{access_token}` (ignore errors).
2. Clear store, cancel API calls, keep downloads and cache.
3. Emit `SignedOut` → UI resets.

---

## 7. Format & quality resolution

### 7.1 Preference chains by asset type

Derived from `listing_type`, `category.slug`, and the format list:

| Asset kind | Detection | Format order (as shipped in `download/resolver.py`) |
|---|---|---|
| Surface / decal / brush / imperfection / atlas | format `texture-set` present | `texture-set` only (never mixed with meshes) |
| 3D prop | not a plant | `converted-files` → `blend` → `glb` → `fbx` → `gltf` → `usd` → `usdz` → `obj` |
| Plant | `nature-plants` in category path/name, or `3D plant` / `plant` in tags | `fbx` → `converted-files` → `gltf` → `glb` → `usd` → `obj` |
| Unsupported | only `unreal-engine`/`uefn*`/`unity` | none → UI: "Not available for Blender" |

`prefer_format` (`converted` / `fbx` / `gltf`) pins a format only when the listing actually offers it; otherwise the chain above decides. `additional-files` (EXR masters) is never auto-selected.

### 7.2 Quality parsing

```python
SURFACE_Q = {"1k": "low", "2k": "mid", "4k": "high", "8k": "raw"}
ASSET_Q   = {"low": "low", "mid": "mid", "high": "high", "raw": "raw"}
# surface: r"_(1k|2k|4k|8k)\."        asset: r"_(low|mid|high|raw)\."
```
`AssetFile.quality` is `None` when neither pattern matches (e.g. single-file listings, `_exr_raw`).

### 7.3 Selection algorithm

```
resolve(listing, format_files, wanted_quality, wanted_format=None) -> Plan
 1. format = wanted_format if user pinned and available, else first in preference chain
 2. candidates = files with quality != None
 3. exact = candidate with quality == wanted
 4. if exact: pick; note = ""
 5. else: nearest lower tier (raw→high→mid→low); if none lower, nearest higher
         note = "4K not available for this asset; using 2K"
 6. if no candidates at all: pick the largest file (single-file formats), note = "single file"
 7. Plan(format_code, file_uid, filename, size, quality=resolved, notes=[...])
```

### 7.4 UI contract

- The selected asset's details render **inline, directly under its own card** in Results: title/hero stay on the card, then Download & Import, the context line, the formats grid, the Files fold and Open on Fab. There is no separate Selected Asset panel, so selecting an asset never scrolls its actions away from the card.
- Formats are a wrapped two-column grid with the chosen one marked `•`.
- The **Files** fold is inline, closed by default, and toggled by `fab_bridge.toggle_files`. Its header states the texture resolution span (`Files (1K–4K)`), and each tier row reads `Low · 1K` / `Mid · 2K` / `High · 4K` / `Raw · 8K` with its size and its own **Get** button (`fab_bridge.download` with `quality` + `format_code` pinned).
- The primary **Download & Import** button pins the format shown in that file list and uses the per-kind default tier, so what the panel shows is what downloads.
- A resolved fallback ("High not available; using Mid") is reported on the job after resolution; there is no pre-download Plan dialog and no >1 GB confirmation in 0.1.0.

---

## 8. Download pipeline

### 8.1 Job state machine

```
queued → resolving → downloading → verifying → extracting → importing → done
   └──────────┴──────────────┴───────────┴────────────┴──────────► failed(reason)
   └──────────┴──────────────┴───────────┴────────────┴──────────► cancelled
```

Transitions are emitted as `StageChanged(job_id, stage)`; progress as `Progress(job_id, bytes_done, bytes_total, speed_bps)` at ≤4 Hz.

### 8.2 Steps (as shipped in `download/manager.py`)

1. **resolving** — fresh `download-info` for the chosen file (never cache the signed URL); the URL host must be `*.epicgames.com`, `*.epicgamescdn.com`, `*.fab.com` or `*.amazonaws.com` over HTTPS (loopback is allowed only so tests can stream from a local server). Disk-space pre-check with 512 MB headroom.
2. **downloading** — stream to `<zip>.part` in 1 MB chunks with a 4 Hz progress tick; `Range` resume when a `.part` exists and the server answers 206. A stale `.part` larger than the expected file is discarded first.
3. **verifying** — `zipfile.ZipFile(...).namelist()` must parse; on failure the `.part` is deleted and the whole attempt is retried **once**, then `ZipCorrupt` is reported.
4. **extracting** — safe extraction into `<asset_dir>/<quality>/<format_code>/`, rejecting absolute paths, `..` traversal, symlinks and `__MACOSX` forked files. The ZIP is deleted unless `prefs.keep_archives` is on.
5. **importing** — `tasks.post(...)`: the only main-thread step (§4.2).
6. **done** — index row written before the import hand-off, so a crashed import does not force a re-download; a cache hit re-imports without touching the network.

### 8.3 Target layout (as shipped)

```
<library_root>/
  Asphalt Road [fc52e5d3]/
    high/texture-set/             # extracted maps for that tier
    mid/texture-set/
    _archives/                    # only when prefs.keep_archives is on
      high-asphalt_road_tiggcjdo_4k.zip
  Rock Sandstone [66f241e5]/
    low/converted-files/
  .fab_index.sqlite
```

`<library_root>` default: `~/Documents/FabAssets` (pref, expanded with `~`/environment variables and created on first use). Thumbnail and hero caches live in Blender's own cache dir, not in the library:
`user_resource('CACHE')/fab_bridge/thumbs/<uid>/<width>-<hash>.jpg`.

### 8.4 Cancellation & resume

- Cancel sets a `threading.Event`; the streaming loop checks every chunk, closes the response, and leaves the `.part` in place with the note "the partial file is kept for resume".
- The next job for the same `(uid, format, quality)` resumes it with `Range`.
- Resume is keyed by the resolved tier, not by a stored size: a `.part` bigger than the freshly resolved file is thrown away rather than continued.
- Verified: the unit suite interrupts and resumes against a local HTTP server; killing Blender mid-download leaves a resumable `.part`.

### 8.5 Disk accounting

- The index keeps `SUM(bytes)`; the Queue panel shows "Cache: 12.4 GB (34 assets)" without creating the index merely because the sidebar was opened.
- `cache_limit_gb` is registered but **not drawn**: nothing enforces a limit yet, and a control that does nothing is worse than no control.
- Re-downloading a tier replaces its extracted folder; "delete files" UI (`mark_deleted`) exists in the store but is not wired to a button in 0.1.0.

---

## 9. Index (SQLite)

`<library_root>/.fab_index.sqlite`, WAL mode, `busy_timeout=15000`, short-lived connections guarded by a lock (every connection is closed explicitly, so Windows never holds the file open).

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

-- One row per listing the user has downloaded.
CREATE TABLE assets(
  uid TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  seller TEXT,
  listing_type TEXT,
  category TEXT,
  last_quality TEXT,               -- tier of the most recent download
  last_format TEXT,
  updated_at REAL
);

-- One row per concrete download: the cache key.
CREATE TABLE files(
  uid TEXT NOT NULL,
  format TEXT NOT NULL,
  quality TEXT NOT NULL,
  path TEXT NOT NULL,
  bytes INTEGER NOT NULL DEFAULT 0,
  filename TEXT,
  imported_at REAL,
  deleted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(uid, format, quality)
);
```

`library` and `claims` (§6.5) are **not created** in 0.1.0: the My Library tab is deferred, and an empty table would imply a feature that does not exist. `meta.schema_version` is written at creation.

On corruption (`sqlite3.DatabaseError`), the file is renamed to `.fab_index.sqlite.corrupt-<ts>` and recreated. A failed write is logged and never breaks the panel; extracted folders remain usable by browsing the library root.

---

## 10. Import specification

### 10.1 Dispatch (as shipped in `importer.py`)

| Extension | Operator | Notes |
|---|---|---|
| `.gltf` / `.glb` | `bpy.ops.import_scene.gltf(filepath=...)` | preferred inside `converted-files` |
| `.fbx` | `bpy.ops.import_scene.fbx(filepath=...)` | importer defaults; axis flags are not forced (unverified against a real prop) |
| `.usd/.usda/.usdc/.usdz` | `bpy.ops.wm.usd_import` | Fab's smallest `converted-files` packs are often `.usdz` |
| `.obj` | `bpy.ops.wm.obj_import` (fallback `import_scene.obj`) | |
| `.blend` | `bpy.data.libraries.load` (append) | native files |

- Mesh discovery order inside a pack: `.glb` → `.gltf` → `.fbx` → `.usdz/.usdc/.usda/.usd` → `.obj`; a texture-set pack has no mesh and takes the surface path.
- Import target collection: `FAB_<AssetTitle>` (created if absent); every new object is moved there.
- Root: when the import produces two or more top-level objects, they are parented to a new empty named `<AssetTitle>`.
- Selection after import: the imported objects; no viewport framing.
- A pack whose importer brought no usable material is rebuilt from the sibling maps; a material the importer did bring is left alone.

### 10.2 Material builder (surface / texture-set)

Channels recognised from filenames (case-insensitive, most specific token first):

| Filename token | Channel | Color space | Destination |
|---|---|---|---|
| `Albedo`, `BaseColor`, `Base_Color`, `Diffuse`, `Diff`, `Color`, `Col` | albedo | sRGB | Base Color (×AO if AO present) |
| `Normal`, `Nor`, `Nrm` | normal | Non-Color | Normal Map → Normal |
| `Roughness`, `Rough` | roughness | Non-Color | Roughness (R) |
| `Gloss`, `Glossiness` | gloss | Non-Color | invert → Roughness (only if no roughness) |
| `Metalness`, `Metallic`, `Metal` | metal | Non-Color | Metallic (R) |
| `AO`, `AmbientOcclusion`, `Occlusion` | ao | Non-Color | multiply into Base Color (Fac 0.5) |
| `Displacement`, `Disp`, `Height` | displacement | Non-Color | Displacement.Height |
| `Bump` | bump | Non-Color | Bump → Normal (only when displacement is off) |
| `Specular`, `Spec` | specular | Non-Color | `Specular IOR Level` (only when no metal map) |
| `Opacity`, `Alpha`, `Mask` | opacity | Non-Color | Alpha (+ material set to dithered) |
| `Emission`, `Emissive` | emission | sRGB | Emission Color + Strength 5 |
| `Translucency`, `Translucent` | translucency | — | recognised but not wired in 0.1.0 |
| `Cavity` | cavity | — | recognised but not wired in 0.1.0 |

When a channel exists at several resolutions the largest wins.

Graph (node names fixed for tests):

```
TexCoord(UV) → Mapping(Vector) ─┬─ Image(albedo, sRGB) ──[×AO]──► Principled.Base Color
                                ├─ Image(roughness, NC) ─────────► Principled.Roughness
                                ├─ Image(metal, NC) → Separate R ► Principled.Metallic
                                ├─ Image(specular) ─────────────► Principled.Specular IOR Level
                                ├─ Image(normal, NC) → [flip G] → NormalMap → Principled.Normal
                                └─ Image(displacement, NC) → Displacement(Scale, Midlevel)
                                                               → Material Output.Displacement
```

- Principled defaults: Metallic 0 (unless metal asset), IOR 1.45, Specular 0, Coat 0.
- Material name `M_<AssetTitle>`; `use_nodes = True`; `displacement_method = 'BOTH'` where supported.
- Displacement `Scale = json.displacementScale` (fallback 1.0), `Midlevel = json.displacementBias` (fallback 0.5). `prefs.displacement` (default on) removes the node when off.
- Normal G-flip: `auto` flips for `fbx` sources only (UE convention), `always` / `never` override.
- `prefs.add_subdivision` (default off): a Subdivision Surface modifier (levels 2) on the generated preview plane.
- `prefs.pack_images` (default off): pack every loaded map into the .blend.
- A texture set lands on the active mesh when it has no material yet; otherwise a 1×1 plane is created, scaled to the asset's real-world size, and named after the asset.

### 10.3 Tiling

- Mapping node scale default: `1 / physical_size` per axis, where `physical_size` comes from the asset JSON (`"2x2"` → 0.5, 0.5; missing → 2×2 m).
- The generated preview plane is already cut to `physical_size`, so its mapping scale is 1.0 — one tile across the plane.
- `prefs.tiling_mode`: `Physical` (default) or `None` (plain 0–1 UVs). There is no stochastic/fit-object mode in 0.1.0.

### 10.4 Plants

- In 0.1.0 a plant is just a mesh: `pick_format` prefers FBX, the mesh is imported, and opacity/alpha maps are wired if the pack has them.
- Variation (`Var*`) selection, LOD choice and the translucency shader branch are **not implemented**; `import_all_variations` is registered but hidden because nothing reads it.

### 10.5 Props / 3D assets

- `converted-files`: import the best mesh in the pack and keep the material the importer produced when it has one; otherwise rebuild from the sibling maps.
- `fbx`/`gltf`: import mesh, then build the material from the sibling maps (same rule: only when the import left no material).
- No raw-tier triangle-count warning in 0.1.0; the tier is chosen per listing kind and can be pinned per row.

### 10.6 Asset Browser (not in 0.1.0)

- Writing `<library_root>/<Category>/<AssetTitle>.blend`, `asset_mark()` and catalog files is **not implemented**. `write_asset_blend` is registered but hidden for that reason.
- Imports land in the scene; the addon never modifies the user's Asset Browser preferences.

### 10.7 Naming rules

| Thing | Pattern |
|---|---|
| Collection | `FAB_<AssetTitle>` |
| Root empty | `<AssetTitle>` (only when the import has 2+ top-levels) |
| Material | `M_<AssetTitle>` |
| Images | filename as downloaded (keeps channel tokens for debugging) |
| Object | importer default; no collision renaming |

*No preview-import path: v1 shows stills only (§12.5). The first byte written to disk is the asset the user asked for.*

---

## 11. Error taxonomy & messages

| Class | Trigger | UI message |
|---|---|---|
| `AuthRequired` | no bundle | "Sign in to Fab to download assets." |
| `AuthExpired` | refresh failed | "Your Epic session expired. Sign in again." |
| `OnlineAccessDisabled` | `bpy.app.online_access == False` | "Online access is off. Enable Preferences > System > Network." |
| `RateLimited(retry_after)` | 429 exhausted | "Fab is rate-limiting requests. Try again in {n} s." |
| `NotEntitled` | 403 on download-info | "This asset isn't in your library. Claim it on Fab first." |
| `UnsupportedFormat` | no usable format | "This listing has no Blender-importable files (UE-only)." |
| `ApiChanged` | 404/parse failure on a known endpoint | "Fab's API changed. Check for an addon update." |
| `DiskFull` | `disk_usage.free < size + 512MB` | "Not enough free space: need {x} GB." |
| `ZipCorrupt` | CRC failure | "Download was corrupted. Retrying / delete and retry." |
| `ImportFailed(exc)` | importer raised | "Import failed: {message}" + log with traceback |
| `NetworkError` | timeouts after retries | "Network error. Check your connection." |

Logging: `logging.getLogger("fab_bridge")`, file handler in `user_resource('CONFIG')/fab_bridge/fab_bridge.log` (rotating, 2 × 2 MB) + panel log view. **Redaction rule:** never log tokens, auth codes, signed URLs (strip query strings), or full cookies.

---

## 12. UI specification

### 12.1 Layout (3D Viewport sidebar, tab "Fab")

```
[ Fab Bridge — online access off ]        (drawn instead of every other panel)
  ⚠ Online access is disabled
    Blender blocks addon network calls until you allow them.
    Preferences > System > Network > Allow Online Access
    [Open Network Preferences]

[ Account ]
  ● <DisplayName>                    [⟳ check] [⏏ sign out]
    1.9 h left
  ○ Not signed in
    [ Sign in with Epic ]                      (primary, full width)
    [ Paste a code ]
    ⓘ Epic ends on a JSON page.
      Ctrl+A then Ctrl+C copies it.
  ⏳ Waiting for the Epic code…
    1. Sign in on the page that opened.
    2. Press Ctrl+A then Ctrl+C.
    3. Blender does the rest.
    [Cancel] [Paste instead]
     (driven by `ui.ops.LOGIN`; replaces the buttons while the modal runs)

[ Search ]
  ( q ......................................... ) (🔍)
  [Relevance ▾]  2,556 assets
  [☑ Blender-ready] [Clear]              (Clear only while a chip is active)

[ Filters ]  (collapsible; counts come from the facet call)
  Type:      [All] [Surfaces 11.0k] [3D 8.8k] [Decals 2.2k] [Brushes 0]
  Category:  ▾ nature-plants
               ▾ rocks-stones 2,696
                 cliffs          579
  Style:     anime  cartoon  stylized  voxel  lowpoly  realistic  pixel
  Theme:     medieval  scifi  cyberpunk  ancient  victorian  …
  Technical: [pbr] [seamless] [modular] [animated] [rigged] …
  ☑ Only show assets Blender can import  (hides 529)

[ Results ]  (cards; **1 column by default**, 128px thumbs)
  ┌────────────────────┐
  │   hero image       │  selected card shows the hero still
  │ Asphalt Road       │
  │ Surfaces · Texture Set
  │ [✓ Selected]   [↗] │   ← every card carries a visible button
  └────────────────────┘
  ┌─────────────────────────────────┐   ← inline details, directly under the
  │ [Download & Import]             │     selected card (no separate panel)
  │ Asphalt · Quixel Megascans      │  one context line, not two
  │ Formats                         │
  │ • Texture Set      GLTF         │  wrapped two per row
  │ Additional files   …            │
  │ ▸ Files (1K–8K)                 │  inline fold, closed by default
  │ [Open on Fab]                   │
  └─────────────────────────────────┘
  (when the fold is open:)
  │ ▾ Files (1K–8K)                 │
  │   Low · 1K     9 MB [Get]       │
  │   Mid · 2K    34 MB [Get]       │
  │   High · 4K  112 MB [Get]       │
  │   Raw · 8K   280 MB [Get]       │
  ┌────────────────────┐
  │      [thumb]       │
  │ Tundra Grass       │
  │ 3D · glTF          │
  │ [Formats]      [↗] │
  └────────────────────┘
  ┌────────────────────┐
  │      [thumb]       │
  │ UE Brush           │
  │ Not for Blender    │
  │ [See on Fab]    [↗]│
  └────────────────────┘
                                   [Load more]

[ Queue ]
  Asphalt Road · High · ▓▓▓▓▓▓░░ 71% · 8.2 MB/s   [Cancel]
  Tundra Grass · Low · queued                       [Cancel]
  Cache: 12.4 GB (34 assets)                     [Manage]
```

### 12.2 Interaction rules

- Search debounce 400 ms; Enter searches immediately; empty query + Megascans filter lists newest.
- Every filter change fires **one** facet request (`count=0&aggregateOn=listing_type`, same params) plus one results request. Counts and the result total both come from the facet response; results paginate independently.
- Facet counts are absolute, not "N+". Because a facet query carries the other filters, clicking a chip shows the count it will actually produce, and chips with 0 are disabled rather than hidden (so the panel doesn't jump).
- Category tree is deferred to M6: rendering it needs the 407-row taxonomy (17 paginated calls) cached with a 7-day TTL. Until then categories are reachable through `q`; `Query.category` is already wired so only the picker is missing.
- Sort offers exactly five values, hard-coded from §5.3. Never let the user type a sort key.
- "Only show assets Blender can import" appends the four `asset_formats` values and stays on across sessions while the Megascans tab is active.
- Grid loads thumbnails from disk cache; if absent, worker downloads the 320 px variant → main thread `pcoll.load`.
- The grid is **one column by default**. Measured in a real GUI session (2026-11-16) the sidebar is ≈205 px wide; two columns clipped every title to "Asphalt R…" and every caption to "Surfaces · Tex…". `thumbnail_columns` = 2 or 3 stays available for users who widen the sidebar.
- The clear-filters (X) button appears only while a chip (type/tag/category) is active — the text query has its own field, and `clear_filters()` keeps the text.
- Detail wraps formats two per row: one per row turned six format names into a scrolling panel, and three per row overflowed the same 205 px width.
- Texture resolution is explicit: the Files header reads `Files (1K–8K)` and every row reads `Low · 1K` / `Mid · 2K` / `High · 4K` / `Raw · 8K`. A bare tier name never told a texture user what they were downloading.
- The card's **Formats** button selects (does **not** download); each card also carries an **Open on Fab** icon and `[See on Fab]` for unsupported assets, and a selected card says `[✓ Selected]`.
- The selected asset has one primary **Download & Import** button, rendered inline under its card; the per-tier **Get** buttons live in the inline **Files** fold, so the quality can still be chosen explicitly without pushing the grid down.
- Quality defaults persist per asset kind (`surface`, `prop`, `plant`) in prefs.
- Buttons disable with a note explaining why (signed out, no usable format); the download button is disabled until sign-in.
- The queue survives panel switches; it is driven by the manager, not the UI.

### 12.3 Operators (as shipped)

| Operator | Type | Notes |
|---|---|---|
| `fab_bridge.login` | modal | opens the copy-code URL; polls a copied code, and also a `https://localhost` listener when `auto_capture` is on. `ESC` cancels |
| `fab_bridge.login_paste` | modal | paste dialog seeded from the clipboard |
| `fab_bridge.login_clipboard` | exec | stop the listener for this attempt (copy-only) |
| `fab_bridge.cancel_login` | exec | stop waiting |
| `fab_bridge.sign_out` | exec | |
| `fab_bridge.check_session` | exec | refresh the token if there is one |
| `fab_bridge.open_network_prefs` | exec | opens Preferences → System; the escape hatch from the offline banner |
| `fab_bridge.search` | exec | generation-gated |
| `fab_bridge.load_more` | exec | |
| `fab_bridge.select_listing` | exec | fetch detail; the details draw under that card |
| `fab_bridge.toggle_files` | exec | open/close the inline Files fold |
| `fab_bridge.set_type` / `fab_bridge.toggle_tag` / `fab_bridge.clear_filters` | exec | filter chips |
| `fab_bridge.download` | exec | enqueue a job; accepts `uid`, optional `quality`, optional `format_code` (the detail panel pins both) |
| `fab_bridge.cancel_download` | exec | per job; the `.part` is kept so it resumes |
| `fab_bridge.clear_downloads` | exec | remove finished rows from the panel |
| `fab_bridge.open_fab` | exec | `webbrowser.open(canonical listing URL)` |

Not present in 0.1.0 (designed, not shipped): `claim`, `claim_all_megascans`, `sync_library`, `import_cached`, `open_media`, `fit_texture`, `login_pkce`.

### 12.4 Preferences

Drawn controls are the ones that change behaviour. Rows marked **hidden** stay registered so existing configs keep loading, but `prefs.draw()` does not show them (a control that does nothing is worse than no control).

| Key | Default | Notes |
|---|---|---|
| `library_root` | `~/Documents/FabAssets` | expanded on use; created if missing (not validated eagerly) |
| `default_quality_surface` | `mid` | |
| `default_quality_prop` | `high` | |
| `default_quality_plant` | `mid` | |
| `prefer_format` | `auto` | auto / converted / fbx / gltf |
| `flip_normals` | `auto` | auto (flip FBX sources) / always / never |
| `tiling_mode` | `physical` | physical / none |
| `displacement` | `on` | wire the displacement node when the pack has a height map |
| `add_subdivision` | `off` | subdivision on the generated plane |
| `pack_images` | `off` | pack loaded maps into the .blend |
| `keep_archives` | `off` | keep the downloaded ZIP next to the extracted folder |
| `thumbnail_columns` | `auto` (`auto` = 1) | auto / 1 / 2 / 3; auto is one column because the sidebar is ~205 px |
| `hero_width` | `1920` | hero image width to fetch, clamped 320–2560 |
| `default_sort` | `-relevance` | one of the five validated values |
| `importable_only` | `on` | append the four Blender-usable `asset_formats` |
| `auto_capture` | `on` | also listen on `https://localhost` for the link on Epic's JSON page; off = clipboard only |
| `log_level` | `info` | |
| `max_workers` | `3` | **hidden** — downloads are one worker per job with no cap wired |
| `cache_limit_gb` | `0` | **hidden** — nothing enforces a limit |
| `write_asset_blend` | `on` | **hidden** — Asset Browser export is not implemented |
| `import_all_variations` | `off` | **hidden** — plant variation selection is not implemented |
| `experimental_pkce` | `off` | **hidden** — PKCE loopback is not validated against a real account |

### 12.5 Previews

Three different things get called "preview", and Fab only gives us two of them.

**What the API actually returns** (measured live 2026-11-16):

| Media | `thumbnails[]` (card) | `medias[]` (gallery) | `type: "model"` | `type: "video"` |
|---|---|---|---|---|
| Surface | 8 sizes: 144→2560 | 2–4 images @ 2560×1440 | — | — |
| Prop / rock | 6 sizes: 144→1280 | up to 14 images @ 1280×720 | yes | sometimes |
| Plant | 6 sizes: 144→1280 | exactly 1 image @ 1280×720 | **no** | **no** |

`medias[].type == "model"` is the 3D spin viewer on the website. `mediaUrl` points at `https://www.fab.com/dope/<previewUid>`, which is an **HTML page** running a Sketchfab-based viewer; the real payload is
`https://media.fab.com/model_previews/<previewUid>/files/<fileUid>/model_file.binz`
— an opaque binary scene archive (28 MB for one prop, plus `file.binz` and loose `textures/*.png`). It is **not glTF and not importable**.

**Decision: stills only.** No 3D preview in v1, and no "download the low tier to look at it" path either. Reasons:

- Fab publishes no preview mesh, and the only 3D artefact it does publish (`.binz`, 28 MB) needs a Sketchfab scene loader to read — far more work than the importer itself.
- A preview that silently downloads 3–9 MB per hover is a disk and bandwidth bug waiting to happen, and it pollutes the cache with files the user never chose.
- There is already a correct escape hatch: `Open on Fab` opens the listing with their real viewer, at zero cost and zero divergence from the source of truth.

If a 3D look-before-download ever becomes a requirement, the honest implementation is a deliberate "Preview at Low" that downloads `converted-files` `_low` into a throwaway collection — not a background behaviour. That is parked, not planned.

#### Image previews

- Two tiers only: **320 px** for grid cards, **largest ≤1920 px** for the hero (props get 1280 from `medias[]`, surfaces get 1920 — prefer the gallery image over the `model` poster, which caps at 1060 and is softer).
- Cache: `<user_resource('CACHE')>/fab_bridge/thumbs/<uid>/<width>.jpg`, immutable — filenames carry no expiry, so a cached file is reused forever.
- Loading rule: `bpy.utils.previews` **cannot load from bytes or URLs** — it needs a real file path, and since 5.x it is a *submodule*: `import bpy` alone does not put it on `bpy.utils`, so the code does `from bpy.utils import previews` lazily and caches the module. (Bug found and fixed in a 5.3 GUI session: `bpy.utils.previews` raised `AttributeError` for every thumbnail.) The flow stays `worker downloads to cache → main-thread tick → pcoll.load(key, path, 'IMAGE')`; the panel never loads inside `draw()`.
- A preview that fails to load or download is recorded in `_failed` and never retried or allowed to escape `_load()`; a broken image must not kill the main-thread pump.
- `pcoll` hygiene: `pcoll.remove(key)` on refresh, and `pcoll.remove()` for every entry in `unregister()`; a leaked collection makes Blender log preview-image errors. LRU cap of 256 entries.
- Request budget: `media.fab.com` is a *different host* from `www.fab.com/i`, so it gets its own bucket — 3 req/s, 4 concurrent, no interaction with the API limiter. Prefetch only the first 12 cards of a page; the rest load when scrolled into view or on "Load more".
- Gallery: hero image + `<n>/<total>` stepper, each gallery image fetched lazily at ≤1920 px. Clicking the hero advances to the next image; `Open on Fab` covers the rest.
- Video listings: Blender cannot play video in a panel, so show the poster frame from `video.images[]` with a `▶ on Fab` badge. Never fake a player.
- Asset Browser: for `write_asset_blend`, call `bpy.ops.ed.lib_id_load_custom_preview(filepath=<640px thumb>)` on the root object so the asset appears with the real Fab image instead of a grey cube.

---

## 13. Testing

### 13.1 Unit (no Blender)
- Quality parsing/resolution table (including missing tiers, single-file formats, `_exr_raw`).
- Filename → channel mapping (all Megascans tokens, mixed case, `T_..._N.jpg`, `..._2K_Normal.jpg`).
- Zip-slip guard: `../`, absolute paths, drive letters, symlink entries, NUL bytes.
- Token store round-trip (per OS path mocked), refresh single-flight, expiry maths.
- Retry/backoff with a fake clock + fake transport (`429` with/without `Retry-After`, `500`, `401` once).
- Pagination loop guard; cursor dedupe.
- SQLite migrations + corruption recovery.

### 13.2 API fixtures
Record real responses (search page, listing detail, `asset-formats/texture-set`, `fbx`, `converted-files`, `download-info`) as JSON in `tests/fixtures/`. Tests parse them into models and assert plans:

| Fixture | Expected |
|---|---|
| surface 4 tiers | `high` → `_4k.zip`, 112,487,573 B |
| decal 8 tiers (8K present) | `raw` → `_8k.zip` |
| prop with converted-files | `converted-files` preferred, glTF file |
| plant | `fbx`, LOD0 |
| UE-only listing | `UnsupportedFormat` |
| missing tier (only 2K/4K) | `raw` → 4K + note |

### 13.3 Blender headless
`blender -b --factory-startup --python dev/blender_smoke.py`
- Import a checked-in surface fixture (1K) and assert: material exists, 6 texture nodes, color spaces, Normal Map connected, Displacement scale/midlevel, Mapping scale.
- Import a plant fixture and assert alpha/translucency wiring + chosen variation/LOD.
- Import a glTF fixture and assert object count, collection membership, asset-marked.
- `pcoll` empties on `unregister()` (assert the previews collection has 0 entries; re-run `register()` and confirm thumbnails reload).
- Hero picker: given a surface payload (thumbnails to 2560, media to 2560) and a plant payload (single 1280 media, no `model`), returns the expected URL and never an empty string.
- `register()`/`unregister()` twice with no leaked timers/handlers.
- Live variant (`dev/blender_smoke.py`) runs against the real API: register → consent gate → search operator → paginate → detail → thumbnail/hero load → teardown. Verified on 5.2.4 LTS, 5.3.0 and 5.4.0-alpha (2026-11-16); exit code 0 and every check true.
- `--factory-startup` has online access **off**, so the live script sets `preferences.system.use_online_access = True` (and first asserts the gate cancels `fab_bridge.search` while it is off).

### 13.4 Real Blender GUI (layout gate)
`blender --factory-startup --python dev/gui_smoke.py -- FABBRIDGE_PT_results --files`
- `Region.active_panel_category` is read-only, so the sidebar tab cannot be selected from Python. Instead the script drives a live search + selection and opens the panel with `bpy.ops.wm.call_panel(name=...)`, which draws it with a **real** `UILayout`, then screenshots the window.
- This is what caught the `bpy.utils.previews` breakage, the 2-column clipping and the stray clear-filters button — none of which a headless run or a fake layout can see.

### 13.5 Manual matrix (release gate)

| Axis | Values |
|---|---|
| Blender | 4.2 LTS, current 5.x |
| OS | Windows, macOS, Linux |
| Asset | surface, decal, prop, plant |
| Quality | low, high, raw (spot) |
| Flows | copy-code login, expired token refresh, cancel mid-download, resume after kill, disk-full simulation, 429 burst, sign-out mid-queue, library sync, gallery stepper |

---

## 14. Spikes (time-boxed, before/while building)

| # | Spike | Output | Blocking? |
|---|---|---|---|
| S1 | Claim with Bearer: does `add-to-library` need `X-CsrfToken`? | boolean + fallback path | yes (claim) |
| S2 | `GET /i/library/search` shape (fields, filters) | fixture + `LibraryItem` parser | yes (library tab) |
| S3 | FBX axis/scale for Megascans props/plants | exact importer kwargs | yes (import) |
| S4 | Normal convention: render `texture-set` Normal with/without G flip | default for `flip_normals=auto` | yes (materials) |
| S5 | PKCE loopback accepted for `egs-desktop`? | keep/drop Flow B | no (opt-in) |
| S6 | Range/206 support + parallel-stream behaviour on the CDN | resume strategy | no |
| S7 | `download-info` via `/i/library/assets/...` vs `/i/listings/...` | preferred route | no |

---

## 15. Diagnostics & configuration

- Constants module `api/endpoints.py`: base URL, OAuth host, media CDN host (`media.fab.com`, separate rate budget), client ids, fixed params (`platform`), `API_SCHEMA = 1`.
- Panel "Diagnostics" section: addon version, Blender version, OS, signed-in account, last API error (sanitised), library root, cache size, API schema.
- `FAB_BRIDGE_BASE_URL` env var override (for future migration to another host without a release).
- On `ApiChanged`, write a single-line diagnostic file the user can paste into an issue.

---

## 16. Milestones

| M | Deliverable | Acceptance |
|---|---|---|
| M1 ✅ | Auth (Flow A: localhost capture + clipboard) + token store + prefs + online-access guard | sign in, restart Blender, still signed in; sign out clears; expired token refreshes; localhost listener with bundled cert, clipboard fallback. Shipped and verified in 5.2.4 / 5.3.0 / 5.4.0-alpha except a live Epic sign-in (needs a real code — see §18). |
| M2 ✅ | API client + search + listing detail + thumbnails | search Megascans, paginate 5 pages, open a listing, see formats/sizes. Delivered: search box (400 ms debounce), type facet chips with live counts, curated tag chips, importable-only toggle, five-value sort, **1-column** thumbnail grid (`auto`), load-more, detail with per-tier sizes. Verified in real Blender 5.2.4 / 5.3.0 / 5.4.0-alpha headless, plus a real GUI layout pass (`dev/gui_smoke.py`). Category picker deferred to M6. |
| M3 ✅ | Resolver + download manager + extract + index + importer | **Delivered in 0.1.0**: format/quality resolution, signed CDN URL per attempt, `.part` streaming with `Range` resume, safe extraction, SQLite index, cache-hit re-import, per-tier **Get** buttons, Queue panel. Verified live in Blender 5.3.0 (`dev/download_smoke.py`): a 9.2 MB texture set and a 2.1 MB USDZ prop downloaded, extracted and imported (9-map `M_Asphalt Road` on a 2×2 m plane; 5 objects in `FAB_Rock Sandstone`). 155 unit tests. |
| M4 ◐ | Surface importer (materials, tiling, displacement) | **Shipped ahead of schedule**: albedo×AO, roughness/gloss, metal, specular, normal (with FBX G-flip), displacement + scale/bias, bump, opacity, emission, physical-size tiling, subdivision, pack-images. Live-verified on Asphalt Road. Remaining: translucent/cavity branches, stochastic tiling. |
| M5 ◐ | Prop + plant importers | Mesh import works for glTF/GLB/FBX/OBJ/USD/USDZ/blend, collection + root empty, material rebuild when the importer brought none. Live-verified on a converted-files USDZ prop. Remaining: plant variation/LOD selection, the translucency shader, FBX axis convention confirmation. |
| M6 ◐ | Queue UI + error taxonomy + logging + gallery hero | Queue panel with text progress bar, speed, cancel, clear, cache line; typed errors + rotating log shipped. Not done: 5-parallel-job cap (one worker per job, no `max_workers`), gallery stepper (hero shows the best still only). |
| M7 | Library tab + claim + owned badges | **Deferred out of 0.1.0**: needs a live account for `/i/library/search`, claim/CSRF and entitlement behaviour. `Query`/`api` seams exist; the tab is not shipped rather than shipped unverified. |
| M8 ◐ | Asset Browser output + docs + packaging | Packaging done: `blender_manifest.toml`, build excludes tests, install-file + enable verified, bundled localhost cert. Docs: `RESEARCH.md` / `SPEC.md`. Not done: `.blend` asset export with previews, README/publish listing text. |

Flow B (PKCE) and "claim all Megascans" land after M8 unless S5/§7 speed them up.

---

## 17. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Fab changes `/i` API | addon breaks | isolate in `api/fab.py`; tolerant parsers; `ApiChanged` with actionable message; version endpoint constants; quick release path |
| Bearer rejected for claim (CSRF) | claims fail | S1 spike; fallback: send `X-CsrfToken` from session cookie if present |
| Anonymous downloads close | downloads fail for signed-out | already require sign-in for downloads (§6.6) |
| Rate limits during claim-all | 429 lockouts | single-flight, 320 ms, exponential backoff, resumable, explicit opt-in |
| Legal/ToS concerns | takedown | official-ish OAuth, own account only, no asset redistribution, disclaimer, no paid-asset bypass, rate politeness |
| Blender API drift (5.x) | importer errors | version gates per feature; headless smoke on 5.2/5.3/5.4 per release; GUI layout pass via `wm.call_panel` |
| Disk pressure from 8K/EXR | user pain | sizes everywhere, disk pre-check, cache manager, no auto-delete |
| Large ZIP extraction blocking UI | frozen UI | extraction in worker thread; only import touches `bpy` |

---

## 18. Open questions (tracked, not blocking design)

1. Exact `library/search` field set and whether `sellers` filter works there (S2) — blocks the deferred My Library tab (M7).
2. Whether `/i/listings/.../download-info` starts enforcing entitlement for free items (monitor).
3. Long-term token lifetime changes (Epic may shorten refresh); refresh UX must handle re-login cheaply.
4. Whether `converted-files` glTF is generated for *all* props or only newer ones (fallback exists; the live probe found many USDZ-only packs).
5. Asset Browser catalog write rules (one catalog per top-level Fab category vs per asset type).
6. Whether Fab exposes a stable `updatedAt` for "update available" checks on cached assets.
7. **Is Epic's JSON page still the landing page for `launcherAppClient2`, and does its `redirectUrl` link reach the local listener?** The direct `redirectUrl=https://localhost/launcher/authorized` route failed live (the account portal dropped it and landed on Settings), so 0.1.0 opens the copy-code URL and treats the listener as opportunistic. A completed live sign-in is still needed to confirm the JSON page and the click-through.
8. **Does a real account's `download-info` work with the same `require_auth` path?** Anonymous downloads were verified live; the signed-in path is the same request plus a bearer token, but it has not been exercised against a live session.
9. FBX import axis convention for Megascans props (importer defaults are left untouched until checked against a real FBX prop).

---

## Appendix A — Quality/format matrix (verified examples)

| Asset | Format | low | mid | high | raw | extra |
|---|---|---|---|---|---|---|
| Asphalt Road (surface) | `texture-set` | 9.2 MB | 33.5 MB | 112.5 MB | 280.5 MB | `_8k_exr.zip` 2.88 GB |
| Historic Bazaar prop | `fbx` | 6.5 MB / 2.6k tri | 23.0 MB / 5.1k tri | 83.5 MB / 20k tri | 316 MB / 1.03M tri | `_exr_raw.zip` 1.22 GB |
| Tundra Grass (plant) | `fbx` | 7.6 MB | 22.5 MB | 55.9 MB | 144.4 MB | — |
| Historic Bazaar prop | `converted-files` | 3.4 MB glTF | 11.5 MB | 38.7 MB | 155.3 MB | `.glb`, `.usdz` |

## Appendix B — Asset metadata JSON (fields we use)

```
id, name, categories[], tags[], semanticTags{asset_type, minSize, maxSize, region},
physicalSize ("0.26x0.26"), highest_available_res (8192),
displacementScale, displacementBias, texelDensity,
maps[{name, type, colorSpace, bitDepth, resolution, physicalSize, mimeType, uri}],
models[...], previews[...]
```
Parser rules: accept `maps` or `components`; treat missing `physicalSize` as 1 m; clamp `displacementScale` to a sane range (>0, else 1.0).

## Appendix C — Security invariants

1. Ne                                                                                                                                                                                                                                                                                      