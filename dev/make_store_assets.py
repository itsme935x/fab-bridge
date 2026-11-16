"""Build the images for the extensions.blender.org submission.

The screenshots come from `dev/gui_smoke.py` (saved under %TEMP%). This script
crops the panels, drops them on a 1920x1080 canvas with the listing copy, and
writes the icon, featured image, previews and raw crops to `submission/`.

    python dev/make_store_assets.py

Re-run after re-taking the GUI screenshots. Crop boxes are in source pixels and
are tied to the 2560x1381 window used by the current smoke run.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "submission"
RAW = OUT / "raw"
SHOTS = Path(os.environ.get("TEMP", os.path.expanduser("~")))

BOLD = "C:/Windows/Fonts/segoeuib.ttf"
REGULAR = "C:/Windows/Fonts/segoeui.ttf"
LIGHT = "C:/Windows/Fonts/segoeuisl.ttf"

ORANGE = (232, 125, 13)
WHITE = (238, 241, 246)
MUTED = (166, 174, 186)
DIM = (125, 133, 147)

# Source crop boxes: (left, top, right, bottom) in the 2560x1381 screenshot.
CROP_RESULTS = (2330, 150, 2540, 566)
CROP_LOGIN = (2338, 503, 2542, 658)
CROP_QUEUE = (2344, 730, 2538, 992)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def background() -> Image.Image:
    top = np.array([17, 19, 23], dtype=float)
    bottom = np.array([28, 32, 39], dtype=float)
    blend = np.linspace(0.0, 1.0, 1080)[:, None, None]
    pixels = (top * (1.0 - blend) + bottom * blend).astype(np.uint8)
    pixels = np.repeat(pixels, 1920, axis=1)
    image = Image.fromarray(pixels, "RGB").convert("RGBA")

    glow = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    draw.ellipse((1180, -280, 2080, 940), fill=(232, 125, 13, 30))
    glow = glow.filter(ImageFilter.GaussianBlur(180))
    image = Image.alpha_composite(image, glow)

    # A hairline at the very top keeps the canvas from looking flat.
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1920, 3), fill=(232, 125, 13, 190))
    return image


def rounded(image: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *image.size), radius=radius, fill=255)
    result = image.convert("RGBA")
    result.putalpha(mask)
    return result


def place_panel(canvas: Image.Image, panel: Image.Image, xy: tuple[int, int]) -> None:
    shadow = Image.new("RGBA", (panel.width + 80, panel.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (40, 40, 40 + panel.width, 40 + panel.height),
        radius=18,
        fill=(0, 0, 0, 150),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(26))
    canvas.alpha_composite(shadow, (xy[0] - 40, xy[1] - 34))

    canvas.alpha_composite(rounded(panel, 12), xy)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (xy[0], xy[1], xy[0] + panel.width - 1, xy[1] + panel.height - 1),
        radius=12,
        outline=(72, 78, 90, 220),
        width=2,
    )


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=fnt) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_paragraph(
    draw: ImageDraw.ImageDraw,
    text: str,
    fnt,
    xy: tuple[int, int],
    width: int,
    fill,
    line_height: int,
) -> int:
    x, y = xy
    for line in wrap(draw, text, fnt, width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += line_height
    return y


def brand(canvas: Image.Image, icon: Image.Image, y: int = 88) -> None:
    icon = icon.resize((58, 58), Image.LANCZOS)
    canvas.alpha_composite(icon, (120, y))
    draw = ImageDraw.Draw(canvas)
    label = font(BOLD, 22)
    x = 194
    for char in "FAB BRIDGE":
        draw.text((x, y + 15), char, font=label, fill=(214, 220, 230))
        x += draw.textlength(char, font=label) + 4


def preview(
    filename: str,
    icon: Image.Image,
    panel: Image.Image,
    panel_xy: tuple[int, int],
    eyebrow: str,
    headline: str,
    body: str,
    bullets: list[str],
) -> None:
    canvas = background()
    draw = ImageDraw.Draw(canvas)
    place_panel(canvas, panel, panel_xy)
    draw = ImageDraw.Draw(canvas)

    brand(canvas, icon)
    draw = ImageDraw.Draw(canvas)
    draw.text((120, 176), eyebrow.upper(), font=font(BOLD, 20), fill=ORANGE)

    headline_font = font(BOLD, 74)
    y = 218
    for line in headline.split("\n"):
        draw.text((118, y), line, font=headline_font, fill=WHITE)
        y += 86

    y = draw_paragraph(
        draw, body, font(REGULAR, 29), (120, y + 32), 800, MUTED, 43
    )

    y += 30
    bullet_font = font(REGULAR, 26)
    for item in bullets:
        draw.ellipse((122, y + 11, 134, y + 23), fill=ORANGE)
        draw.text((154, y), item, font=bullet_font, fill=(210, 216, 225))
        y += 52

    draw.text(
        (120, 1000),
        "Blender 4.2 LTS and newer  ·  GPL-3.0-or-later  ·  Unofficial",
        font=font(LIGHT, 20),
        fill=DIM,
    )
    canvas.convert("RGB").save(OUT / filename, optimize=True)


def make_icon() -> Image.Image:
    size = 256
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((0, 0, size, size), radius=56, fill=(28, 31, 37, 255))
    draw.rounded_rectangle(
        (1, 1, size - 2, size - 2), radius=55, outline=(58, 63, 74, 255), width=2
    )

    # Isometric cube: three shaded faces, the clearest shorthand for a 3D asset.
    top = [(128, 38), (196, 77), (128, 116), (60, 77)]
    left = [(60, 77), (128, 116), (128, 190), (60, 151)]
    right = [(196, 77), (128, 116), (128, 190), (196, 151)]
    draw.polygon(top, fill=(244, 166, 77, 255))
    draw.polygon(left, fill=(201, 111, 12, 255))
    draw.polygon(right, fill=(156, 82, 0, 255))
    for face in (top, left, right):
        draw.line(face + [face[0]], fill=(255, 217, 166, 255), width=4, joint="curve")

    # Download badge, kept large enough to read at 64 px.
    draw.ellipse((154, 154, 246, 246), fill=(232, 125, 13, 255), outline=(28, 31, 37, 255), width=8)
    draw.rectangle((195, 169, 205, 201), fill=(28, 31, 37, 255))
    draw.polygon([(181, 197), (219, 197), (200, 226)], fill=(28, 31, 37, 255))

    OUT.mkdir(parents=True, exist_ok=True)
    icon.save(OUT / "icon-256.png", optimize=True)
    return icon


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)

    results = Image.open(SHOTS / "fab_bridge_results.png").convert("RGB")
    login = Image.open(SHOTS / "fab_bridge_login.png").convert("RGB")
    queue = Image.open(SHOTS / "fab_bridge_queue.png").convert("RGB")

    raw_results = results.crop(CROP_RESULTS)
    raw_login = login.crop(CROP_LOGIN)
    raw_queue = queue.crop(CROP_QUEUE)
    raw_results.save(RAW / "results-panel.png", optimize=True)
    raw_login.save(RAW / "sign-in.png", optimize=True)
    raw_queue.save(RAW / "queue.png", optimize=True)
    results.save(RAW / "blender-window.png", optimize=True)

    icon = make_icon()

    # Scale by height so the panel's aspect never stretches.
    panel_results = raw_results.resize(
        (round(raw_results.width * 1010 / raw_results.height), 1010), Image.LANCZOS
    )
    panel_login = raw_login.resize((816, 620), Image.LANCZOS)
    panel_queue = raw_queue.resize((582, 786), Image.LANCZOS)

    preview(
        "preview-1-browse.png",
        icon,
        panel_results,
        (1370, 35),
        "Browse",
        "Megascans,\ninside Blender",
        "Search Fab from the 3D viewport. Open a result to see its formats, "
        "file sizes and texture tiers.",
        [
            "No Epic launcher or Unreal Engine",
            "Low (1K) through Raw (8K)",
            "Download, cancel and resume",
        ],
    )
    preview(
        "preview-2-sign-in.png",
        icon,
        panel_login,
        (1000, 300),
        "Sign in",
        "One Epic sign-in",
        "Downloads need an Epic account, even for free assets. Finish on Epic's "
        "page, then click the redirect link or copy the page.",
        [
            "Browser redirect or clipboard",
            "The local certificate warning is expected",
            "Sign out at any time",
        ],
    )
    preview(
        "preview-3-queue.png",
        icon,
        panel_queue,
        (1120, 170),
        "Queue",
        "Every download,\nin one place",
        "Progress, speed, cancel and resume sit in the Queue panel. Finished "
        "downloads are indexed for re-import.",
        [
            "Cancel keeps the partial file",
            "ZIPs are verified before extraction",
            "Re-import without the network",
        ],
    )
    preview(
        "featured-1920x1080.png",
        icon,
        panel_results,
        (1370, 35),
        "Blender extension",
        "Quixel Megascans,\nwithout the launcher.",
        "Fab Bridge puts the Fab catalogue in Blender's sidebar. Search, "
        "download and import with the Epic account you already use.",
        [
            "Search and filter the Megascans catalogue",
            "1K to 8K texture sets with file sizes",
            "Textures and 3D assets import into the scene",
        ],
    )

    print("wrote", OUT)
    for path in sorted(OUT.rglob("*.png")):
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
