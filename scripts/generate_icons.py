#!/usr/bin/env python3
"""Generate Elvern platform icons from the source logo asset.

Reads the canonical logo PNG from the project root and produces all
required icon sizes under frontend/public/icons/ plus the favicon.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    raise SystemExit(
        "Pillow is required: pip install Pillow"
    )

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "Elvern_Official_App_Logo.png"
ICON_DIR = PROJECT_ROOT / "frontend" / "public" / "icons"
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "public"

BACKGROUND_MIN_CHANNEL = 235
BACKGROUND_MAX_CHROMA = 10
EDGE_CROP_PIXELS = 16


def _is_corner_background(pixel: tuple[int, int, int, int]) -> bool:
    r, g, b, a = pixel
    return (
        a > 0
        and min(r, g, b) >= BACKGROUND_MIN_CHANNEL
        and max(r, g, b) - min(r, g, b) <= BACKGROUND_MAX_CHROMA
    )


def _remove_corner_background(src: Image.Image) -> Image.Image:
    """Make only the edge-connected white corner background transparent."""
    rgba = src.convert("RGBA")
    pixels = rgba.load()
    w, h = rgba.size

    transparent = rgba.copy()
    out = transparent.load()
    queue: deque[tuple[int, int]] = deque(
        ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
    )
    seen = set(queue)

    while queue:
        x, y = queue.popleft()
        if not _is_corner_background(pixels[x, y]):
            continue

        r, g, b, _a = out[x, y]
        out[x, y] = (r, g, b, 0)

        for nx, ny in (
            (x - 1, y),
            (x + 1, y),
            (x, y - 1),
            (x, y + 1),
        ):
            if nx < 0 or ny < 0 or nx >= w or ny >= h or (nx, ny) in seen:
                continue
            seen.add((nx, ny))
            queue.append((nx, ny))

    return transparent


def _trim_outer_edge(img: Image.Image) -> Image.Image:
    if EDGE_CROP_PIXELS <= 0:
        return img

    w, h = img.size
    return img.crop(
        (
            EDGE_CROP_PIXELS,
            EDGE_CROP_PIXELS,
            w - EDGE_CROP_PIXELS,
            h - EDGE_CROP_PIXELS,
        )
    )


def _resize(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.LANCZOS)


def _maskable(base: Image.Image, size: int = 512) -> Image.Image:
    icon_size = int(size * 0.80)
    offset = (size - icon_size) // 2
    resized = _resize(base, icon_size)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, (offset, offset), resized)
    return canvas


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Source logo not found: {SOURCE}")

    src = Image.open(SOURCE)
    transparent = _trim_outer_edge(_remove_corner_background(src))

    ICON_DIR.mkdir(parents=True, exist_ok=True)

    icons: list[tuple[str, Image.Image, int]] = [
        ("icon-512.png", transparent, 512),
        ("icon-192.png", transparent, 192),
        ("apple-touch-icon.png", transparent, 180),
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
