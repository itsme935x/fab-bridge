# Fab → Blender addon: research report

**Status:** research only, nothing built yet.
**Date:** 2026-11-16
**Scope:** how a Blender addon can log in to Fab, browse the (now free) Quixel Megascans catalogue, download assets, apply Fab's quality modes, and import them into Blender.

---

## Decisions (2026-11-16)

| Question | Decision |
|---|---|
| v1 scope | **Megascans browse/download** in 0.1.0; the "My library" tab is designed but deferred until a live account can validate `/i/library/search` |
| Blender target | **4.2 LTS → 5.x**, single extension package (`blender_manifest.toml`) |
| Login | **As shipped:** open Epic's copy-code page for the launcher client (`/id/login?redirectUrl=…/id/api/redirect?clientId=…&responseType=code`). A bundled-cert listener on `127.0.0.1:443` is started when `auto_capture` is on, so clicking the JSON page's `redirectUrl` link finishes sign-in automatically; copying the page always works too. A direct `redirectUrl=https://localhost/launcher/authorized` is **not** used — Epic's account portal dropped it and landed on Settings (live, 2026-11-16). **PKCE loopback stays hidden** until a real account validates it. |
| Previews | **Stills only.** Fab publishes no preview mesh; the 3D viewer is a Sketchfab `.binz` archive. Gallery images from `media.fab.com` + "Open on Fab" for a real spin. No preview downloads. |
| Online-access gate | `bpy.app.online_access` defaults to **off** on a fresh profile (verified: `--factory-startup` and an empty `BLENDER_USER_CONFIG` both report `False`). So the panel shows a blocking banner with an **Open Network Preferences** button (`screen.userpref_show(section='SYSTEM')`) instead of just naming the setting. |
| Login landing page | The launcher client has no consent page: Epic is documented to end on a JSON blob (`authorizationCode`, `redirectUrl`, …). That is expected, not an error — so the panel/checklist tells the user to click the redirect link or copy the page, and the parser accepts the whole blob, the `redirectUrl`, or the bare code. A completed live sign-in has not yet been observed here. |
| Token response | `expires_in` + `refresh_expires` are **seconds**, `expires_at` + `refresh_expires_at` are **ISO strings**. Parse both, never `float()` an ISO timestamp (this broke the first real sign-in), and map a `400/401` from the token endpoint to "sign in again", not "API changed". |
| Card affordances | A thumbnail + title reads as a picture, not a control. Every card carries a visible `Formats` button (and a link icon); `Selected Asset` sits **above** Results (`bl_order` 2 vs 3) so the primary button is never a scroll away. |
| Filter widgets | The importable filter is a **checkbox**, not a toggle button — a full-width blue toggle looked like the main action of the panel. |
| Grid columns | **One column by default.** A real GUI pass measured the sidebar at ≈205 px; two columns clipped every title ("Asphalt R…") and caption ("Surfaces · Tex…"). `thumbnail_columns` 2/3 remain opt-in. |

Consequences for the plan:
- The Library tab was deferred: `/i/library/search` needs a live account, and shipping an unverified tab is worse than shipping none. The browse grid therefore has no "In library" badge in 0.1.0.
- Library items may be UE-only or have formats Blender cannot import — detect and show "not available for Blender" instead of failing at download time.
- 4.2+ extensions mean no legacy `bl_info` path, but the `bpy.app.online_access` guard and manifest permissions are mandatory.
- Both login flows share the token store; keep `auth.py` flow-agnostic (it consumes an authorization code / PKCE verifier + redirect URI).
- Stills only means no `.preview/` scratch tree, no `FAB_PREVIEW` collection, and the thumbnail cache is the only image storage the addon owns.

---

## 0. TL;DR

1. **A Fab addon is feasible without any launcher, UE install, or Bridge app.** The Fab web app talks to a plain JSON API on `https://www.fab.com/i/...`. That API is what I reversed and tested.
2. **Login** = Epic Games account OAuth. The Epic Games Launcher's public client (`launcherAppClient2`) can be used with the standard authorization-code flow; the token you get is accepted by the Fab API as `Authorization: Bearer <access_token>`. No scraping, no cookie jar.
3. **Downloads are simple signed ZIPs**, not the UE chunk/manifest system — *for the formats Blender needs* (`fbx`, `gltf`, `converted-files`, `texture-set`, `additional-files`). A single `GET .../download-info` returns a signed CDN URL (valid ≈1 h). UE `.uasset` packs use the chunked manifest path and are **out of scope**.
4. **Quality modes** are a filename suffix convention, not a query parameter: `low / mid / high / raw`, which map to **1K / 2K / 4K / 8K** for texture sets and to mesh-LOD + matching texture tier for 3D assets. There is also a separate EXR/8K "additional files" package.
5. **Import** is well understood: Epic publishes its own Blender plugin (v0.2.16) that only *imports* payloads the launcher sends it. Its material-building code is a solid reference for our own importer. We must write the download/quality side ourselves.
6. **Main risks:** this is an undocumented API (it changed before — an existing community addon is archived for exactly that reason), plus Fab's ToS and rate limits. Design must isolate the API layer and be defensive.

---

## 1. What Fab actually is (and why Megascans is free)

- Fab is Epic's marketplace; **your Fab account *is* your Epic Games account** (single sign-on).
- Assets are *claimed* into your **library** ("Add to my library"), then downloaded. Free ≠ anonymous: Fab expects an account + accepted buyer terms.
- Quixel Megascans were fully released as free content on Fab (Nov 2026): ~**22,600 listings** from the `Quixel Megascans` seller, each at $0 with a *Personal* and a *Professional* license.
- Listings expose `isQuixelClaimable` (bulk-claim flag) and `isFree`; search lets you filter by seller, so "all of Megascans" is addressable as `sellers=o-cbf5bbfdb340d6a839b5520cb31328` (Quixel seller id).

**Implication:** the addon should sign the user in, claim items into their library (1 request, 300 ms apart), then download. Anonymous download technically works for free listings today (see §3.6) — we should *not* build on that, both for correctness and for terms-of-service reasons.

---

## 2. Login: options and recommendation

### 2.1 Client credentials that work (public, shipped inside the launcher binary)

| Client | id | secret | Notes |
|---|---|---|---|
| `launcherAppClient2` | `34a02cf8f4414e29b15921876da36f9a` | `daafbccc737745039dffe53d94fc76cf` | Used by community tools (e.g. `fab-downloader`); works with authorization-code and refresh grants |
| `egs-desktop` (SISU) | `c85f4c6238b04d3399bc0080e83b83fc` | `1371757d-23ee-4676-90cc-f6948e98b7ed` | New launcher browser flow: PKCE + loopback redirect, returns an EAS token that must be exchanged for EG1 |

Token endpoint: `POST https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token`
(Basic auth `base64(client_id:client_secret)`, form-encoded body, `token_type=eg1`.)

Response fields used: `access_token`, `refresh_token`, `expires_in` (~7200 s), `refresh_expires` (~28800 s), `account_id`, `displayName`.

### 2.2 Option A — copy-code page + opportunistic localhost listener

**What 0.1.0 does.** The addon opens the launcher client's documented code page:

`https://www.epicgames.com/id/login?redirectUrl=<urlencoded https://www.epicgames.com/id/api/redirect?clientId=34a02cf8...&responseType=code>`

After sign-in, Epic shows a plain JSON page containing `authorizationCode` (documented by other launcher clients, e.g. Legendary's `get_auth_url()`; not yet observed in a live sign-in here). There are two ways to finish:

1. **Click the `redirectUrl` link on that page.** It points at `https://localhost/launcher/authorized?code=…`, and the addon binds `127.0.0.1:443` with a bundled self-signed certificate, so the code is caught without copying. The browser's certificate warning is expected.
2. **Copy the page** (`Ctrl+A`, `Ctrl+C`). The addon polls the clipboard (`bpy.context.window_manager.clipboard`, needs the `clipboard` extension permission) for up to ~10 min, regex-extracts the code, and stops watching once signed in.

`auto_capture` off (or **Copy only** in the panel) skips the listener; the clipboard still works. Then `POST /account/api/oauth/token` with `grant_type=authorization_code&code=...&token_type=eg1`, store the tokens, and refresh with `grant_type=refresh_token` when `expires_at - now < ~10 min`; on refresh failure, sign out and re-prompt.

**Why not send the browser straight to localhost?** It was tried and failed live (2026-11-16): `https://www.epicgames.com/id/login?redirectUrl=https://localhost/launcher/authorized` lands a signed-in user on `accounts.epicgames.com` account **Settings**. The account portal has moved off `www.epicgames.com`, and it drops the unregistered redirect instead of preserving it. Scripted inspection of the redirect chain is blocked by Cloudflare (403 managed challenge), so the browser behaviour is the evidence. The code page keeps its `redirectUrl` (checked in the in-app browser), which is why automatic capture rides on the link inside it.

Pros: one client, no redirect-URI surprises, works on Windows/macOS/Linux. The clicking path removes the copy when it works; the clipboard remains the guaranteed path.

### 2.3 Option B — PKCE + loopback (nicer UX, needs validation)

The launcher v2 signs in through `https://www.epicgames.com/id/login` with:
`client_id=c85f4c62...&response_type=code&redirect_uri=http://127.0.0.1:<port>/launcher/authorized&scopes=basic_profile friends_list country presence&code_challenge=...&code_challenge_method=S256&state=...`

If Epic accepts an arbitrary loopback port for that client (RFC 8252 style), the addon can run a tiny `http.server` on `127.0.0.1`, capture `?code=&state=`, and never touch the clipboard. Then exchange: PKCE code → EAS token (`token_type=eas`), then `grant_type=exchange_code` → EG1. DPoP is used by the new launcher but is optional server-side.

**Decision (as shipped):** A (copy-code page plus an opportunistic localhost listener) is the only login path in 0.1.0. PKCE stays registered but hidden, because it has not been tested against a real account. Token refresh/storage code is identical for both.

### 2.4 Token storage & security

- Windows: **DPAPI** via `ctypes` (`CryptProtectData`/`CryptUnprotectData`) — same approach as `fab-downloader`.
- macOS: Keychain via `security add-generic-password` / `find-generic-password`.
- Linux: `secret-tool` if present, else a `0600` file in a private dir.
- Store: access token, refresh token, expiry, account id, display name, client id (so a client change invalidates old sessions).
- Never store the Epic password (we never see it — browser only).
- Add `Sign out` which also calls `DELETE /account/api/oauth/sessions/kill/{token}`.

### 2.5 Blender-specific gates

- Extension manifest must declare `[permissions] network = "..."`, `files = "..."`, `clipboard = "..."`.
- Blender 4.2+ has a global **online access** toggle: check `bpy.app.online_access` and fail gracefully with a clear message if disabled. This is required for extensions that use the network.

---

## 3. Fab API used by the addon (all verified live today)

Base: `https://www.fab.com/i`. Public endpoints need no auth *for free listings*; library/claim endpoints need `Authorization: Bearer <access_token>`.

### 3.1 Search / browsing (public)

```
GET /i/listings/search?<filters>&count=24&cursor=<cursors.next>
```
- Page size is capped at **24** even if you ask for more; paginate with `cursor` (`cursors.next` / `next`).
- Useful filters: `q`, `sellers=<seller-id>|Quixel+Megascans`, `listing_types=material|3d-model|...`, `categories=...`, `is_free=1`, `sort_by=-publishedAt|-firstPublishedAt|-relevance`.
- Result fields used by the UI: `uid`, `title`, `isFree`, `startingPrice` (with `offerId`), `licenses[{name,slug,priceTier,offerId}]`, `category`, `tags`, `ratings`, `thumbnails[].images[].url` (144/160/320/640/960/1280/1920/2560 px), `assetFormats[].assetFormatType.code`.

```
GET /i/listings/{uid}                      # full detail: description, medias, licenses, changelogs, techDetails
GET /i/listings/{uid}/asset-formats/{code} # file list + technical specs for one format
```
`asset-formats/{code}` returns `files[]` with `name`, `uid`, `size`, `fileType`, and for 3D: `vertexCount`, `faceCount` — everything needed to build the quality picker and show "1.0M tris / 316 MB".

### 3.2 Claim into library (auth)

```
POST /i/listings/{uid}/add-to-library
     body (form-encoded): offer_id=<free offer id>
```
- Pick the free **Personal** offer: `licenses.find(slug=personal && priceTier.price==0)`.
- A bulk Quixel endpoint exists in the web bundle as `claimQuixelAssets` → `POST /i/entitlements/quixel/claim-assets`, but every variant I tried returns 404 (bare POST, JSON content-type, GET, `/i/portal/...`, trailing slash). So it is either routed from a different origin (the bundle defines several API clients with distinct base URLs) or gated on a body/session I don't have. Treat it as **unavailable**: use per-item claiming. It is a *one-off* of ~22k requests at 300 ms (≈2 h) if the user hits "claim all", so make it optional and resumable.

### 3.3 Library listing (auth)

```
GET /i/library/search?count=24&cursor=...&sort_by=-createdAt
```
(Also `/i/users/me` to check the session; 401 = signed out.)

The UE-specific library endpoint `GET /e/accounts/{accountId}/ue/library` is only for `.uasset` builds — not needed for Blender, but it is the fallback if we ever want UE-format downloads.

### 3.4 Download URL (the important one)

```
GET /i/listings/{uid}/asset-formats/{formatCode}/files/{fileUid}/download-info?platform=Windows
```
Response:
```json
{"downloadInfo":[{"assetFormat":"asset-format/3d-exchange/fbx",
  "downloadUrl":"https://emp-fastly-stitched.epicgamescdn.com/.../asset_low.zip?f_token=...",
  "expires":"2026-11-16T08:00:14Z","type":"binary"}]}
```
- `type: "binary"` → a plain ZIP, download it directly. (UE format returns `type: "manifest"` + chunk CDNs; we ignore that.)
- Signed URL expires (~1 h). **Always request download-info right before downloading** and refresh on 403.
- Another root exists for library assets (`/i/library/assets/{uid}/asset-formats/...`) — use it once entitlements are in place if the listing route ever starts enforcing ownership.

### 3.5 Format inventory (what a Blender addon can consume)

| Format code | Content | Blender use |
|---|---|---|
| `converted-files` | `.gltf`+`.bin` / `.glb` / `.usdz`, textures packed as ORM, glTF-standard normals | **Best default for props** (auto materials in Blender) |
| `fbx` | one mesh per LOD quality + separate JPG maps + `{id}.json` | Best mesh control for props/plants |
| `gltf` | `_ue_` zips: mesh + `B`/`N`/`ORM` textures, UE naming/normal convention | Works, needs normal flip + channel remap |
| `texture-set` | surface maps (`AO, BaseColor, Bump, Cavity, Displacement, Gloss, Normal, Roughness, Specular`) + `{id}.json` | Surfaces/decals/brushes |
| `additional-files` | `*_exr_raw.zip` / `*_8k_exr.zip` (1.2–2.9 GB) | Optional high-end EXR tier |
| `unreal-engine`, `uefn*`, `unity` | `.uasset` / engine packs | **Not supported** (must be detected and greyed out) |
| `usd`, `usdz`, `obj`, `blend` (some listings) | native files | supported by Blender import |

Verified packaging examples:
- 3D asset `fbx` mid (23 MB): `Asset_Mid.fbx` + `Asset_Mid_2K_{Normal,Bump,Gloss,Roughness,Cavity,Specular,BaseColor,AO,Metalness}.jpg` + `xesndip.json`.
- Surface `texture-set` 1K (9 MB): `Asphalt_Road_tiggcjdo_1K_{AO,BaseColor,Bump,Cavity,Displacement,Gloss,Normal,Roughness,Specular}.jpg` + `tiggcjdo.json` (contains `maps[]` with `colorSpace`, `bitDepth`, `resolution`, `physicalSize`, plus `displacementScale=1.970352`, `displacementBias=0.465486`, `texelDensity`, `highest_available_res`).
- Plant `fbx` low (7.6 MB): 9 variations × LOD0–LOD3 `.fbx` + 1K maps. → importer needs LOD/variation selection, not just "one mesh".
- Converted glTF low (3.4 MB): `..._Low.gltf/.bin` + `textures/..._1K_{albedo,orm,normal}.jpeg`.

### 3.6 Things I verified that you should know about

- **Free listings' exchange formats are downloadable without auth today.** `download-info` for a Megascans `fbx`/`texture-set` file returned a signed URL to an anonymous request. Paid listings expose **no files** publicly (empty `files[]` for UE-only; no exchange formats at all for UE-only products), and the private converted-file S3 URL 403s unless signed. So the gate exists but is loose on free content. The addon must still log in and claim — do not ship an anonymous "scraper" mode.
- **Rate limits are real.** The community claimer hit a 10-minute lockout with 8 parallel adds; 1-at-a-time with 300 ms spacing is fine. Handle `429` + `Retry-After` with backoff everywhere (search, claim, download-info, CDN).
- **CSRF:** the website sends `X-CsrfToken` (from the `fab_csrftoken` cookie) for cookie-session calls. With a Bearer token this is normally unnecessary — must be confirmed with a real token (open item, §9).
- **Search page size 24**; ~22.6k items → ~945 pages. Cache aggressively, don't crawl on every keystroke; use `q` + cursor pages with a "load more" button.

### 3.7 Previews (images vs. the 3D viewer)

Every listing carries two image collections and sometimes media:

| `listingType` | `thumbnails[]` | `medias[]` | `type:"video"` | `type:"model"` |
|---|---|---|---|---|
| `material` (surface/decal) | 8 widths, 144→**2560** | 2–4 images @ 2560×1440 | — | — |
| `3d-model` (prop/rock) | 6 widths, 144→**1280** | up to 14 images @ 1280×720 | sometimes (mp4 + DASH + HLS) | **yes** |
| `3d-model` plant | 6 widths, 144→1280 | exactly 1 image @ 1280×720 | — | **no** |

The site's 3D spin viewer is **Sketchfab**, not a Fab mesh: `medias[].type == "model"` → `mediaUrl = https://www.fab.com/dope/<previewUid>` → an HTML page whose payload is
`https://media.fab.com/model_previews/<previewUid>/files/<fileUid>/model_file.binz` (27,986,256 B for one prop, plus `file.binz` 2.5 KB and loose `textures/*.png|*.jpeg`).
It is an opaque Sketchfab scene archive — **not glTF, not importable**, and larger than the asset's own `converted-files` low (3.4 MB). So there is no cheap 3D preview to steal: the honest options are a still image, or downloading the real lowest tier.

Video listings expose `mediaUrl` (mp4) plus `manifestDash`/`manifestHls` on `epic-games-store-cdn.qstv.on.epicgames.com`; useful only as "open on Fab" — a Blender panel cannot play them, but `video.images[]` gives ready-made 320–1920 px poster frames.

There is **no separate preview endpoint**: `/i/listings/{uid}/previews`, `/media`, `/thumbnails`, `/i/model-previews/{uid}`, `/dope/{uid}.json` all 404. Previews exist only as (a) image URLs inside the listing payload, and (b) the Sketchfab archive.

**What other tools do** (checked, not assumed):

| Tool | Card images | 3D preview |
|---|---|---|
| Epic's own Blender plugin v0.2.16 | **none** — grep of `__init__.py`, `payload.py`, `listener.py` finds no `preview`/`thumbnail`/`icon`; `Payload` has no image field at all | none; the launcher owns all browsing UI, the plugin imports blind |
| Fab website | listing images | Sketchfab iframe over the `.binz` |
| Fab UE plugin | engine content browser | real asset in-engine |
| BlenderKit / Poly Haven addons (ecosystem norm) | downloaded thumbs → `bpy.utils.previews` grid | none — stills only, asset imported on demand |
| Quixel Bridge (retired) | local `_thumb.jpg` | real 3D viewport, which requires the low LOD to be on disk |

So "pull the preview from Fab" is already the cheapest possible answer *for stills*, and every shipping tool does exactly that. Nothing in the ecosystem renders a 3D spin without having the geometry locally.

**Decision (2026-11-16): stills only.** No 3D preview in v1. The `.binz` route is 28 MB behind an undocumented Sketchfab loader, and "download a 3–9 MB low tier just to look at it" was rejected as a background behaviour — it spends the user's disk and bandwidth on something they didn't ask for. `Open on Fab` is the escape hatch for a real spin. A deliberate "Preview at Low" operator is parked, not planned.



---

## 4. Quality modes — the exact model

Fab's own front-end defines the quality vocabulary and the mapping (found in the web bundle):

```js
Low = "low"; Mid = "mid"; High = "high"; Raw = "raw"
// texture sets:
{ low: "_1k",  mid: "_2k",  high: "_4k",  raw: "_8k" }   // e.g. tiggcjdo_4k.zip
// everything else:
{ low: "_low", mid: "_mid", high: "_high", raw: "_raw" } // e.g. asset_high.zip
```
Quality is also used for the mesh tier of 3D assets: in our sample the same asset's `fbx` files were
`_low` 2.6k tris/6 MB · `_mid` 5k tris/23 MB · `_high` 20k tris/83 MB · `_raw` 1.0M tris/316 MB,
with maps at 1K/2K/4K/8K respectively.

**So "quality" in this addon = one enum, mapped to files, not to any server parameter.** UI should be:

| Addon label | Enum | Surface (`texture-set`) | 3D asset / plant (`fbx`, `gltf`, `converted-files`) |
|---|---|---|---|
| Draft / Low | `low` | 1K | lowest LOD + 1K maps |
| Standard / Mid | `mid` | 2K | mid LOD + 2K maps |
| High | `high` | 4K | high LOD + 4K maps |
| Raw / Max | `raw` | 8K | raw scan mesh + 8K maps |
| + "EXR masters" | (extra download) | `additional-files` 8K EXR (≈2–3 GB) | `additional-files` `_exr_raw.zip` |

Practical details:
- **Do not assume four tiers.** Enumerate `files[]` and present what exists (decals often have 1K/2K/4K/8K; some assets are 2K/4K only). If a tier is missing, grey it out.
- Show size + tri count per tier from the file list (already in the API) so users can avoid a 300 MB surprise.
- Default: **High** for props, **Mid** for surfaces, remember per-type choice in prefs.
- Quality must be independent of *format*: e.g. `converted-files` glTF exists only for some listings (usually props, not plants) — fall back `converted-files → fbx → gltf → obj/usd → (unsupported)`.

---

## 5. Import: what must happen in Blender

Epic's own Blender plugin (v0.2.16, `cdn.quixel.com/fab/plugins/blender/blender_v0.2.16.zip`) only receives already-extracted local paths over TCP 28889 and imports them. Its material code is the best reference (and shows what Epic considers correct):

- **Channel mapping** (payload keys): `albedo, normal, bump, roughness, glossiness, specular, metal, orm, occlusion, displacement, emission, opacity, translucency, fuzz`.
  Filename → channel: `BaseColor→albedo`, `AO→occlusion`, `Metalness→metal`, `Gloss→glossiness`, `Cavity` (extra), `Displacement`, `Normal`, `Bump`.
- **Principled setup**: sRGB for albedo/specular/emission/translucency; Non-Color for everything else; AO multiplied into base color at Fac 0.5; gloss inverted when no roughness; ORM split into R/G/B → occlusion/roughness/metallic (glTF path); `Sheen Weight` from fuzz; snow/SSS special case.
- **Displacement**: `ShaderNodeDisplacement` with `Scale = displacementScale`, `Midlevel = displacementBias` (from JSON), `material.displacement_method='BOTH'`; skip for plants/high-poly.
- **Plants**: alpha (`opacity`) via transparent+mix or hashed blend, translucency through an add-shader; the official plugin has a dedicated "custom plant setup" branch.
- **Normals**: flip green when the source is UE/DirectX (Fab's DCC catalogue declares `normalMapConvention: "opengl"` for Blender vs `"directx"` for Unreal, and the payload carries `flipnmapgreenchannel` per material). Plan: default *flip for `fbx`/`gltf`/`texture-set`*, *no flip for `converted-files` (glTF)*, plus a user override. **Validate by rendering one surface both ways**; do not hard-code on faith.
- **Tiling**: surfaces should get `Texture Coordinate → Mapping` with scale from the JSON's `physicalSize` (`"2x2"` m) so texel density is right; Blender 4.3+ also offers stochastic/box tiling, which is a nice power feature (opt-in).
- **Scale/orientation**: Megascans meshes are authored Y-up; the official importer relies on importers' defaults and then parents/renames. We should explicitly verify FBX/glTF axis conversion + scale (metres) once, then lock it in.
- **LOD/variations**: plants ship `VarA..VarI × LOD0..3`; 3D assets ship one mesh per *quality* tier. Importer needs: "import LOD0 only" / "import all variations as collection instances".
- **Collections & naming**: root empty named after the asset, all imported objects parented to it, materials named `M_<Asset>`, textures kept on disk and packed optionally. This matches the official plugin's behaviour and keeps the Outliner clean.
- **Asset Browser**: optional but high value — write a `.blend` per downloaded asset, mark objects as assets, and generate catalog files per Fab category so Megascans show up as a library next to the user's own assets.

---

## 6. Proposed addon architecture

```
fab_bridge/
  __init__.py            # register/unregister, prefs, bpy.app.online_access guard
  blender_manifest.toml  # 4.2+ extension (permissions: network, files, clipboard)
  bl_info                # legacy fallback header for 3.6–4.1
  api/
    auth.py              # OAuth device: login URL, code exchange, refresh, DPAPI/keychain store
    client.py            # Fab HTTP client: session, retries/429, cursor pagination, download-info
    models.py            # dataclasses: Listing, Format, AssetFile, Quality, LibraryItem
    errors.py            # typed errors: AuthExpired, RateLimited, NotEntitled, UnsupportedFormat
  download/
    manager.py           # queue + worker threads, resume (.part), zip validation, cancel
    extract.py           # safe extraction (zip-slip guarded), folder layout, metadata index
    cache.py             # JSON/sqlite index: asset id, quality, format, size, mtime, files
  importers/
    dispatch.py          # extension → gltf/fbx/usd/obj/blend importer
    materials.py         # Megascans node graph (port of official plugin logic + tiling)
    plants.py            # opacity/translucency/LOD/variation handling
    assets.py            # collection, parenting, asset-marking, catalogs
  ui/
    panel.py             # N-panel: account, search, filters, quality selector, queue
    browser.py           # results grid with thumbnails (bpy.utils.previews), "Load more"
    library.py           # "My library" / "Downloaded" views, re-import, delete cache
    ops.py               # operators: Login, Search, Claim, Download, Import, Cancel
    queue.py             # bpy.app.timers-driven progress + log
  prefs.py               # download dir, default quality/format, flip normals, tiling, pack images
```

**Threading model (Blender rules):**
- Network + ZIP I/O in `threading.Thread` / small `ThreadPoolExecutor` (2–3 workers, 1 for claim/spam-sensitive calls).
- Workers only touch Python data + disk; **all `bpy` calls happen on the main thread**, drained from a `queue.Queue` by a `bpy.app.timers.register(..., persistent=True)` poller (~10 Hz).
- Import itself (`bpy.ops.import_scene.*`) must run on the main thread — run it as a modal/queued step after extraction, one asset at a time.
- Cancellation: `threading.Event` checked between chunks; delete `.part` on cancel (or keep for resume — user preference).

**Download robustness:**
- Stream to `<file>.part`, HTTP `Range` resume, then verify the ZIP (`zipfile.testzip()`), then atomic rename.
- Refresh `download-info` if the signed URL returns 403 (URLs expire in ~1 h).
- Exponential backoff on 429/5xx honoring `Retry-After` (start 30 s, cap 10 min, like the proven claimer).
- Disk-space pre-check against `file.size`; default library at `~/Documents/FabAssets` (configurable).
- Cache layout: `<root>/<AssetTitle>_<uid8>/<quality>/<format>/...` + `index.json`; dedupe by `uid + quality + format`.
- Thumbnail cache in the addon's user dir (`bpy.utils.user_resource('CACHE')`).

**UI shape:** sidebar tab "Fab" → Account box (avatar/name/sign out) → search + filters (Megascans only, type, free) → thumbnail grid → per-asset panel (formats available, quality radio with sizes, "Download & Import" / "Download only" / "Re-import"). Plus a queue panel with per-item progress and a log. Optional: "Claim all Megascans" with progress + resume (2 h, explicitly opt-in).

---

## 7. Blender platform facts that shape the build

- **Bundled Python already has what we need:** `requests`, `urllib3`, `certifi`, `numpy`, `sqlite3`, `zstandard` are shipped by Blender (confirmed by Blender's own `bl_bundled_modules.py` test). So: use `requests` with `certifi` (no wheels to bundle), `urllib` only as a fallback.
- **Extension packaging (4.2+):** `blender_manifest.toml`, `schema_version = "1.0.0"`, `type = "add-on"`, `license = ["SPDX:GPL-2.0-or-later"]` (GPL is the safe choice for Blender addons), `blender_version_min = "4.2.0"`, `[permissions] network/files/clipboard` with short reasons.
- **Blender 3.6–4.1** would need the legacy `bl_info` path — possible, but 4.2+ (and definitely 5.x) should be the target; the official Fab plugin claims 3.6+, we don't have to.
- **Image loading:** `bpy.data.images.load()` on JPG/EXR/PNG works out of the box; set `colorspace_settings.name` per channel. EXR tier is 16/32-bit and heavy — warn before importing.
- **Blender 5.x** is current on this machine (configs for 5.2/5.3/5.4); test on at least 4.2 LTS + current 5.x.
- **`bpy.app.online_access`** must be checked (4.2+); show a clear "enable online access in Preferences → System" message instead of failing silently.

---

## 8. Prior art (what to reuse / avoid)

| Project | What it proves | Takeaway |
|---|---|---|
| `Fobal/fab-downloader` (Python, GPL-3) | Full working auth + UE artifact/manifest download, no launcher | Best reference for auth + retry/backoff + DPAPI storage; its UE chunk code is out of scope for us |
| Epic's `blender_v0.2.16.zip` (official Fab Blender plugin) | Import-only; payload schema; material graph; plants | **Port/adapt the material + plant logic.** It also confirms quality/format vocabulary |
| `cgmaterial/fab-to-blender` (archived) | A Blender addon that browsed Fab inside Blender | Archived because "Fab changed their API" → isolate the API layer, version it, fail gracefully |
| `EXIVIZ/megascans-to-fab-library`, `RyensX/UnrealFabAssistant` | Bulk claiming of all Megascans works | Claim endpoint + offer selection + rate-limit strategy (300 ms, 429 backoff, resumable) |
| `drune9d/blender-asset-importer`, Lightcraft batch importer | ZIP → PBR material heuristics for Fab/Megascans | Useful fallback mapping table & ZIP-slip hardening patterns |
| `Zyhloh/Unofficial-EGL-Documentation` | Documented Epic launcher endpoints/clients | Auth client ids, token shapes, `GetManifest`, plugin catalogue URL |

Also useful: `https://quixel-megascans-static.s3.us-west-2.amazonaws.com/fab/manifest/prod/plugins_v1.json` — Epic's own DCC catalogue (Blender 0.2.16, port 28889, `normalMapConvention: opengl`, supported formats/versions). Good sanity check for what Epic supports.

---

## 9. Open questions — must be validated with a real account

1. Does `POST /i/listings/{uid}/add-to-library` accept a **Bearer** token without `X-CsrfToken`? (If not: obtain a CSRF token via the login redirect or fall back to a `requests.Session` cookie flow.)
2. Exact shape of `GET /i/library/search` (fields, filtering by seller/format) — needed for the "My library" tab.
3. Refresh cadence/limits: `expires_in=7200`, `refresh_expires=28800` — confirm behaviour after long idle; what happens on `refresh` while a download URL is live (URLs are independent of the token).
4. Does `/id/login` accept a **loopback redirect** for `egs-desktop` (Option B)? If yes, drop the clipboard step.
5. Is the bulk `POST /i/entitlements/quixel/claim-assets` reachable at all? (Bundle shows `claimQuixelAssets`; all plain attempts 404. If it exists it cuts claim-all from ~2 h to minutes.)
6. Normal-map convention ground truth: render one surface with and without green flip in Blender (agent: compare against the Fab-launcher export if available).
7. FBX/glTF axis + scale for Megascans meshes (Y-up → Z-up) and whether `converted-files` glTF imports at the right size.
8. Whether `download-info` on `/i/library/assets/...` behaves differently from `/i/listings/...` once the item is owned (better for long-term stability).
9. CDN behaviour for large files: Range support, throttle per connection, whether parallel connections help or trigger limits.

---

## 10. Risks & legal / product guardrails

- **Undocumented API.** Fab can change paths or add auth requirements at any time (precedent: `fab-to-blender`). Mitigation: single API module, typed errors, "Fab changed something" diagnostics, remote-config-able endpoints, no hard-coded assumptions about response extras.
- **ToS.** The Fab/EULA governs use of assets; programmatic access is not officially supported. Rules to build in:
  - user's **own** Epic account only (OAuth, never username/password);
  - never redistribute assets or bundle them with the addon;
  - do not expose a "download any paid asset" path — only free/owned items, gated by the user's entitlements;
  - polite rate limits (single-flight claims, 300 ms spacing, exponential backoff, no crawling the whole catalogue on idle);
  - clear "unofficial, not affiliated with Epic" disclaimer, and a nod to Fab's own Blender plugin as the supported alternative.
- **Client secret shipping.** The launcher client id/secret are public (compiled into `EpicGamesLauncher.exe`) and used by community tools, but shipping them is still a gray area — Option B (PKCE) avoids embedding a secret. Document the choice.
- **Bandwidth/disk reality.** Raw 3D assets are 300 MB; 8K EXR packs are 1–3 GB; users will fill disks. Show sizes everywhere, pre-check free space, offer "delete after import (keep .blend)".
- **Licensing of the addon:** GPL-2.0-or-later / GPL-3.0-or-later (Blender ecosystem norm). Do not copy GPL code without attribution (`fab-downloader` is GPL-3 — reference only, or attribute if reused).
- **Support burden:** Blender version churn (4.2 LTS → 5.x), importer API changes (`bpy.ops.import_scene.fbx` args), and Epic-side changes. Keep a diagnostics panel with versions + last API error.

---

## 11. Suggested delivery plan

**MVP (prove the loop)**
1. Auth (Option A) + secure store + `online_access` guard + "signed in as X".
2. Search Megascans (seller filter) with thumbnails + pagination; free-only enforcement.
3. Format/quality picker from `asset-formats` (sizes + tris), download with progress, safe extract, cache.
4. Import: surfaces → material with tiling + displacement; props → glTF `converted-files` (fallback FBX) + material; plants → FBX LOD0 + opacity/translucency.
5. Queue UI + cancel + error log.

**Phase 2:** library tab + auto-claim, "claim all Megascans" (resumable), assets browser integration, EXR tier, re-import/update, batch download, disk management.

**Phase 3:** PKCE loopback login, non-Megascans free assets, USD/native `.blend` support, material presets (stochastic tiling, parallax), optional UE-format export hints.

---

## 12. Verification status (0.1.0, 2026-11-16)

Proof for the claims in this report, and the gaps that remain.

| Area | Verified how | Status |
|---|---|---|
| Search / detail / formats / file sizes | live API, `dev/blender_smoke.py`, `dev/gui_smoke.py` screenshots | ✅ |
| CDN download + resume + extract | live 9.2 MB texture set and 2.14 MB USDZ prop in Blender 5.3.0 (`dev/download_smoke.py`) | ✅ |
| Importer (material graph, plane sizing, mesh collection) | same smoke: 9-map `M_Asphalt Road` on a 2×2 m plane; 5 objects in `FAB_Rock Sandstone` | ✅ |
| Index + cache re-import | smoke: row written, second job imports without a network call | ✅ |
| Queue UI, per-tier Get buttons, sign-in gate | real GUI layout pass, screenshots | ✅ |
| Inline details under the selected card, Files fold, 1K–8K labels | real GUI layout pass (`dev/gui_smoke.py -- FABBRIDGE_PT_results --files`, 2026-11-16) | ✅ |
| Packaging | `extension build`, `extension validate`, `install-file --enable`, enable check in a temp extension dir (6 panels, 18 operators) | ✅ |
| Unit suite | 160 tests (`python -m unittest discover -s fab_bridge/tests -t .`) | ✅ |
| Live Epic sign-in (click the redirect link or copy the page) | needs a real authorization code | ⛔ unverified |
| Epic's code page keeps its `redirectUrl` | real-browser load (in-app Chromium, 2026-11-16): sign-in form renders at the same URL | ✅ |
| `redirectUrl=https://localhost/launcher/authorized` accepted by Epic | tried live 2026-11-16: portal dropped it and landed on account Settings | ❌ rejected |
| Signed-in `download-info` (bearer token) | anonymous path verified; same request + token not exercised | ⛔ unverified |
| My Library / claim / entitlement | deferred out of 0.1.0 | — |
| FBX axis + normal convention on a real prop | only USDZ/glTF props were imported live | ⛔ unverified |

**CDN host observed live:** `content-download-emp.distro.on.epicgames.com` (a subdomain of `epicgames.com`, so the allowlist covers `*.epicgames.com`, `*.epicgamescdn.com`, `*.fab.com`, `*.amazonaws.com` and requires HTTPS).

**Live finding worth keeping:** many of the smallest `converted-files` packs are `.usdz`, not glTF, so `find_import_file` ranks `glb → gltf → fbx → usdz/usdc/usda/usd → obj` and the importer dispatches `.usdz` through `wm.usd_import`.

---

## Appendix A — sample evidence (captured today)

`GET /i/listings/f22f56e0.../asset-formats/fbx` (3D prop):
```
raw  3,160,042,425 B  1,026,568 tris  (raw scan mesh)
high    83,482,667 B     20,127 tris
mid     23,029,160 B      5,079 tris
low      6,482,462 B      2,644 tris
```

`GET /i/listings/fc52e5d3.../asset-formats/texture-set` (asphalt surface):
```
asphalt_road_tiggcjdo_8k.zip  280,495,203 B
asphalt_road_tiggcjdo_4k.zip  112,487,573 B
asphalt_road_tiggcjdo_2k.zip   33,533,358 B
asphalt_road_tiggcjdo_1k.zip    9,227,395 B
```
plus `additional-files`: `asphalt_road_tiggcjdo_8k_exr.zip` 2.88 GB.

`GET /i/listings/{uid}/asset-formats/texture-set/files/{fileUid}/download-info?platform=Windows`:
```json
{"downloadInfo":[{"assetFormat":"asset-format/texture-set",
 "downloadUrl":"https://content-download-emp.distro.on.epicgames.com/...zip?cf_token=...",
 "expires":"2026-11-16T08:00:55.967Z","type":"binary"}]}
```

Texture-set JSON (`tiggcjdo.json`): `maps[]` with `type/colorSpace/bitDepth/resolution/physicalSize/mimeType/uri`,
`displacementScale: 1.970352`, `displacementBias: 0.465486`, `texelDensity: 4096`, `highest_available_res: 8192`,
`semanticTags` (asset_type, categories, region…).

## Appendix B — key URLs

- Fab API: `https://www.fab.com/i/...`
- Epic OAuth: `https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token`
- Login page: `https://www.epicgames.com/id/login?redirectUrl=...`
- Code page: `https://www.epicgames.com/id/api/redirect?clientId=34a02cf8f4414e29b15921876da36f9a&responseType=code`
- DCC plugin catalogue: `https://quixel-megascans-static.s3.us-west-2.amazonaws.com/fab/manifest/prod/plugins_v1.json`
- Official Blender plugin: `https://cdn.quixel.com/fab/plugins/blender/blender_v0.2.16.zip`
- Quixel seller filter id: `o-cbf5bbfdb340d6a839b5520cb31328`
