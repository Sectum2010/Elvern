#!/usr/bin/env python3
"""Generate Elvern platform icons from the source logo asset.

Reads the canonical logo PNG from the project root and produces all
required icon sizes under frontend/public/icons/ plus the favicon.
"""
from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    raise SystemExit(
        "Pillow is required: pip install Pillow"
    )

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "Elvern_New_Example_Logo_512x512.png"
ICON_DIR = PROJECT_ROOT / "frontend" / "public" / "icons"
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "public"

# Background colour that matches the icon's own dark fill.
BG_COLOR = (11, 15, 20, 255)


def _crop_icon(src: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Return (transparent-bg, opaque-bg) crops of the logo squircle."""
    rgba = src.convert("RGBA")
    pixels = rgba.load()
    w, h = rgba.size

    # The source image is the squircle icon centred on a white background.
    # Crop to a tight square around the icon body.
    cx, cy = w // 2, h // 2
    half = 120  # captures the full squircle (~234-236 px) + glow margin
    box = (cx - half, cy - half, cx + half + 1, cy + half + 1)

    transparent = rgba.crop(box).copy()
    tp = transparent.load()
    tw, th = transparent.size
    for y in range(th):
        for x in range(tw):
            r, g, b, a = tp[x, y]
            if (r + g + b) / 3 > 235:
                tp[x, y] = (r, g, b, 0)

    opaque = transparent.copy()
    op = opaque.load()
    for y in range(th):
        for x in range(tw):
            r, g, b, a = op[x, y]
            if a == 0:
                op[x, y] = BG_COLOR

    return transparent, opaque


def _resize(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.LANCZOS)


def _maskable(base: Image.Image, size: int = 512) -> Image.Image:
    icon_size = int(size * 0.80)
    offset = (size - icon_size) // 2
    resized = _resize(base, icon_size)
    canvas = Image.new("RGBA", (size, size), BG_COLOR)
    canvas.paste(resized, (offset, offset), resized)
    return canvas


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Source logo not found: {SOURCE}")

    src = Image.open(SOURCE)
    transparent, opaque = _crop_icon(src)

    ICON_DIR.mkdir(parents=True, exist_ok=True)

    icons: list[tuple[str, Image.Image, int]] = [
        ("icon-512.png", transparent, 512),
        ("icon-192.png", transparent, 192),
        ("apple-touch-icon.png", opaque, 180),
    ]

    for name, base, size in icons:
        out = ICON_DIR / name
        _resize(base, size).save(out, "PNG", optimize=True)
        print(f"  {out.relative_to(PROJECT_ROOT)}  ({size}x{size})")

    maskable_path = ICON_DIR / "icon-maskable.png"
    _maskable(transparent).save(maskable_path, "PNG", optimize=True)
    print(f"  {maskable_path.relative_to(PROJECT_ROOT)}  (512x512 maskable)")

    favicon_path = PUBLIC_DIR / "favicon.png"
    _resize(transparent, 32).save(favicon_path, "PNG", optimize=True)
    print(f"  {favicon_path.relative_to(PROJECT_ROOT)}  (32x32 favicon)")

    ico_path = PUBLIC_DIR / "favicon.ico"
    _resize(transparent, 48).save(
        ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)]
    )
    print(f"  {ico_path.relative_to(PROJECT_ROOT)}  (16/32/48 ico)")


if __name__ == "__main__":
    main()
