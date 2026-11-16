# Fab Bridge

Browse Quixel Megascans on Fab from inside Blender, download a surface or a 3D
asset, and get a shaded object in the scene. No Epic Games Launcher, no Unreal
Engine, no Quixel Bridge.

Unofficial. Not affiliated with Epic Games or Quixel.

## What it does

- Search the Megascans catalogue with type/tag filters, sort, and a
  "Blender-ready" filter that hides listings Blender cannot read.
- See every file Fab ships for a listing: tier, size, and format.
- Download with progress, cancel and resume; the ZIP is verified and extracted
  next to your other assets.
- Import:
  - **Surfaces** get a Principled material built from the maps (albedo ×AO,
    roughness or gloss, metal, specular, normal, displacement or bump,
    opacity, emission) on a plane cut to the scan's real-world size, or on the
    active empty mesh.
  - **3D assets** import through glTF/GLB, FBX, OBJ, USD/USDZ or `.blend` into
    a `FAB_<title>` collection, with the material rebuilt from the maps when
    the file did not bring one.
- Re-import from the local index without downloading again.

## Requirements

- Blender 4.2 LTS or newer (tested on 5.2.4, 5.3.0 and 5.4.0-alpha).
- An Epic Games account (the same account you use on Fab). Downloads need
  sign-in even for free assets.
- Online access enabled in Blender: **Preferences → System → Network**.

## Install

1. Download `fab_bridge-0.1.0.zip`.
2. In Blender: **Edit → Preferences → Get Extensions**, open the menu at the
   top right and choose **Install from Disk…**, then pick the ZIP.
3. Enable **Fab Bridge** if it is not already enabled.
4. Open the 3D viewport sidebar (`N`) and choose the **Fab** tab.

## Verify the download

You do not have to trust the ZIP. Build it yourself from this repository and
compare the contents file by file:

```
git clone https://github.com/itsme935x/fab-bridge
cd fab-bridge
blender --factory-startup --command extension build --source-dir fab_bridge --output-dir dist
```

The published `fab_bridge-0.1.0.zip` has SHA-256:

```
ACCF6AEECCE1B191ECD8D405D1EE165C255AE623B5D7B887D3F774CF4B24E76C
```

A rebuild will not byte-match that hash: the ZIP stores file timestamps, so two
builds of the same source differ. Compare what is inside instead.

```
python -c "import hashlib,zipfile;z=zipfile.ZipFile('dist/fab_bridge-0.1.0.zip');[print(hashlib.sha256(z.read(n)).hexdigest(),n) for n in z.namelist() if not n.endswith('/')]"
```

Every `.py` and `.toml` entry should hash the same as the file of the same name
under `fab_bridge/`.

What the addon talks to, and nothing else:

- `www.fab.com` — search, listings, download-info.
- `www.epicgames.com` and `account-public-service-prod03.ol.epicgames.com` — sign-in and token refresh.
- `cdn.fab.com` / Fab's media hosts — thumbnails.
- `https://localhost` — the redirect listener, on your own machine only.

No telemetry, no analytics, no update check, no call home. The log is local and
redacts tokens and query strings before writing. The bundled
`fab_bridge/auth/localhost.{crt,key}` is a self-signed certificate for
`localhost`, generated for the redirect listener; it is not a secret and
verifies nothing outside your machine.

## Sign in

1. Press **Sign in**. The addon opens Epic's sign-in page in your browser.
2. Sign in. Epic finishes on a JSON page that contains an `authorizationCode`.
3. Either click the `redirectUrl` link that page shows — Blender is listening
   on `https://localhost/launcher/authorized` and catches the code — or copy the
   page with Ctrl+A then Ctrl+C and Blender reads it from the clipboard.

The local link uses a certificate bundled with the addon, so the browser warns
that the connection is not private. That warning is expected: the certificate
exists only so the redirect can be read on your own machine.

If port 443 is already in use, the listener cannot start and the panel offers
**copy the code instead**. Copy the page Epic shows you, and the addon reads the
code from the clipboard without any further click.

## Download an asset

- Press **Formats** on a card to select it. Its download button, formats and
  files appear **directly under that card** — there is no separate details panel
  to scroll to.
- The **Files** drop-down lists the downloadable tiers and states the texture
  resolution next to each one: `Low · 1K`, `Mid · 2K`, `High · 4K`, `Raw · 8K`.
- **Download & Import** uses the default tier for the asset kind (2K for
  surfaces and plants, 4K for props).
- Or open **Files** and press **Get** next to a specific tier.

Progress, speed, cancel and cache size live in the **Queue** panel. Cancel
keeps the partial file; downloading the same tier again resumes it.

## Where files go

Default library: `~/Documents/FabAssets` (change it in the addon preferences).

```
FabAssets/
  Asphalt Road [fc52e5d3]/
    high/texture-set/           extracted maps
    _archives/                  only if "Keep ZIPs" is on
  .fab_index.sqlite             what has been downloaded
```

Thumbnails live in Blender's own cache directory, not in the library.

## Preferences

- **Library Folder** — where downloads are extracted.
- **Surface / Prop / Plant Quality** — default tier per asset kind.
- **Preferred Format** — Automatic, glTF (converted), FBX or glTF (UE), used
  only when the listing offers it.
- **Normal Map Green** — Automatic flips FBX sources only; override per need.
- **Tiling** — Physical Size or None.
- **Use Displacement**, **Add Subdivision**, **Pack Images**.
- **Keep ZIPs** — keep the downloaded archive next to the extracted folder.
- **Catch the browser sign-in** — the localhost listener; turn off for
  copy-code mode.
- **Preview Width**, **Grid Columns**, **Sort**, **Only Show Importable**.

## Known limitations in 0.1.0

- **My Library** is not in this build. Browsing what you already own needs
  `/i/library/search`, which could not be validated without a live account.
- The live Epic sign-in path has not been exercised end to end yet: the API
  calls, listener and token parsing are tested, but a real authorization code
  was not available during the build. The clipboard path is the fallback.
- Signed-in downloads use the same `download-info` request that was verified
  anonymously; the bearer-token variant has not been tested against a live
  session.
- Plant support is mesh import plus an alpha map. Variation and LOD selection
  and the translucency shader are not implemented.
- FBX axis conversion is left at Blender's importer defaults.
- No Asset Browser export, no 3D previews, no claim/checkout.
- Fab's API is undocumented and can change. Errors say so instead of failing
  silently; the log is in Blender's config directory under
  `fab_bridge/fab_bridge.log`.

## Development

```
python -m unittest discover -s fab_bridge/tests -t .        # 160 tests, stub bpy
```

Real-Blender checks:

```
blender --factory-startup --background --python dev/download_smoke.py   # live download + import
blender --factory-startup --python dev/gui_smoke.py -- FABBRIDGE_PT_results
blender --factory-startup --python dev/gui_smoke.py -- FABBRIDGE_PT_results --files
```

Build and install the extension:

```
blender --factory-startup --command extension build --source-dir fab_bridge --output-dir dist
blender --command extension install-file -r user_default --enable dist/fab_bridge-0.1.0.zip
```

Design notes live in `SPEC.md`; the API research and its verification status
live in `RESEARCH.md`.

## License

GPL-3.0-or-later. See `LICENSE`.
