# Fab Bridge — extensions.blender.org submission

Everything needed for the draft at
<https://extensions.blender.org/add-ons/fab-bridge/draft/>.

Upload artifact: `dist/fab_bridge-0.1.0.zip`
(81,654 bytes, 35 files, includes `LICENSE`).

| Field | Value |
| --- | --- |
| Name | Fab Bridge |
| Version | 0.1.0 |
| License | GPL-3.0-or-later |
| Blender | 4.2.0 and newer |
| Type | Add-on |
| Tags | Import-Export, Material |
| Support URL | `https://github.com/itsme935x/FaB-QUIXEL` — **confirm before submitting** |

---

## 1. Name

```
Fab Bridge
```

## 2. Tagline

Store limit is 64 characters. Current manifest tagline is 47.

```
Browse and import Fab / Quixel Megascans assets
```

## 3. Description

Markdown. Paste as-is.

```markdown
**The Quixel Megascans catalogue, in Blender's 3D viewport.** Search Fab, pick an
asset, download it, and import a shaded object — no Epic Games Launcher, no
Unreal Engine, no Quixel Bridge.

Unofficial. Not affiliated with Epic Games or Quixel.

### Browse

- Search the Megascans catalogue from the sidebar (**N** panel → **Fab**).
- Filter by asset type and tags, sort results, and hide listings Blender cannot
  read.
- Every card shows the formats and files Fab ships: tier, size, format.
- Details open directly under the card you clicked, so there is no second panel
  to hunt for.

### Download

- Progress, speed, cancel and resume in the **Queue** panel.
- Cancel keeps the partial file. Starting the same tier again resumes it.
- ZIPs are verified before extraction, and archive paths are sanitised.
- Finished assets are indexed locally, so you can re-import later without the
  network.

### Import

- **Surfaces** get a Principled material built from the maps — albedo × AO,
  roughness or gloss, metal, specular, normal, displacement or bump, opacity,
  emission — on a plane cut to the scan's real-world size, or on an empty mesh
  you already selected.
- **3D assets** import through glTF/GLB, FBX, OBJ, USD/USDZ or `.blend` into a
  `FAB_<title>` collection. If the file brings no material, one is rebuilt from
  the texture maps.

### Texture tiers

Quality is the tier name, and the panel prints the resolution next to it:
**Low · 1K**, **Mid · 2K**, **High · 4K**, **Raw · 8K**.

### Requirements

- Blender 4.2 LTS or newer.
- An Epic Games account — the same one you use on Fab. Downloads need sign-in
  even for free listings.
- Online access enabled in **Preferences → System → Network**.

### Signing in

Press **Sign in** and Epic's page opens in your browser. Finish there, then click
the redirect link so Blender can catch the code, or copy the page and let the
addon read it from the clipboard. The browser warns that the `localhost`
connection is not private — that is expected, the certificate exists only so the
redirect can be read on your own machine.

### Not in 0.1.0

- My Library, or browsing assets you already own.
- Claiming assets or checkout.
- 3D previews.
- Plant variation and LOD selection.

Fab's API is undocumented and can change without notice. Failures are reported in
the panel and written to `fab_bridge.log` in Blender's config folder.

GPL-3.0-or-later. The extension ships no Epic content; assets download from Fab
under your own account.
```

## 4. Release notes

```markdown
First release.

- Browse the Megascans catalogue with search, type and tag filters, sorting, and
  a filter that hides files Blender cannot read.
- Per-asset file list with tier, size, format and resolution (1K–8K).
- Downloads with progress, speed, cancel and resume. ZIPs are verified and
  extracted safely.
- Surface materials built from the texture maps on a correctly sized plane.
- 3D assets imported from glTF/GLB, FBX, OBJ, USD/USDZ and `.blend`.
- Local index for re-importing without downloading again.
- Epic sign-in by browser redirect or clipboard.
```

## 5. Support URL

```
https://github.com/itsme935x/FaB-QUIXEL
```

The upload form wants a page users can reach when something breaks. Use the repo
if it is public and Issues are enabled; otherwise use a dedicated issues URL or
your Fab/profile page. **Do not submit with a dead link** — the review team
checks it.

## 6. Tags

```
Import-Export
Material
```

## 7. Images

All previews are 1920×1080. The form needs the icon, the featured image, and at
least one 16:9 preview.

| File | Size | Where it goes |
| --- | --- | --- |
| `icon-256.png` | 256×256 | Icon |
| `featured-1920x1080.png` | 1920×1080 | Featured image |
| `preview-1-browse.png` | 1920×1080 | Preview — catalogue and file tiers |
| `preview-2-sign-in.png` | 1920×1080 | Preview — Epic sign-in |
| `preview-3-queue.png` | 1920×1080 | Preview — download queue |

`raw/` holds the uncomposited crops plus the full Blender window. It is not for
upload; it exists so the previews can be rebuilt with
`python dev/make_store_assets.py`.

Order the previews 1 → 3. Browse first: it shows the most in one frame.

## 8. Before you submit

- [ ] `dist/fab_bridge-0.1.0.zip` was rebuilt after the license change to
      GPL-3.0-or-later. Older zips are rejected by the upload page.
- [ ] Support URL resolves and Issues are open.
- [ ] Description matches the build: no My Library, no claiming, no 3D previews.
- [ ] Version in `blender_manifest.toml` is 0.1.0, and it matches this listing.
- [ ] Install it once on 4.2 LTS. The manifest minimum is 4.2.0, but the builds
      tested here were 5.2.4, 5.3.0 and 5.4.0-alpha. Keep "Blender 4.2 LTS or
      newer" as the stated requirement, and fix the minimum if 4.2 breaks.
- [ ] Gallery images are in the order above.
- [ ] The listing keeps the "Unofficial, not affiliated with Epic Games or
      Quixel" line. It is the cheapest protection against a takedown.

## 9. Accuracy notes

Claims in the copy that are backed by the build or by live testing:

- Search, filters, sorting, file list, sizes, formats — verified against the
  live Fab API and by the GUI smoke run.
- Download, cancel, resume, ZIP verification, extraction, re-import — covered by
  `dev/download_smoke.py`.
- Surface material and 3D import — exercised in the live smoke run.
- Sign-in flow, clipboard parsing, listener — unit tested. **A real Epic
  authorization code was not exercised**, so the copy says how the flow works
  rather than promising it always succeeds.
- Signed-in downloads reuse the `download-info` request that was verified
  anonymously; the bearer-token variant is untested against a live session.

Claims deliberately left out: My Library, asset claiming, Unreal-only formats,
plant variation, LOD control, and any performance or speed number.
