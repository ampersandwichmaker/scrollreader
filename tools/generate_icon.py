#!/usr/bin/env python3
"""
Generate a simple ScrollReader icon (assets/icon.png + icon.ico).
Run this once locally, then commit the assets/ folder.

Requires: pip install Pillow
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import struct, zlib, io

def make_icon(size=256):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark background circle
    pad = size // 16
    draw.ellipse([pad, pad, size-pad, size-pad], fill=(26, 26, 26, 255))

    # Red triangle (the ▶ indicator, centred)
    cx, cy = size // 2, size // 2
    r = size // 4
    tri = [
        (cx - r//2, cy - r),
        (cx - r//2, cy + r),
        (cx + r,    cy),
    ]
    draw.polygon(tri, fill=(255, 68, 68, 255))

    # Thin horizontal lines (representing text)
    lw = int(size * 0.32)
    lh = max(2, size // 40)
    lx = cx + r//3
    gap = size // 9
    for i, alpha in enumerate([180, 140, 100]):
        ly = cy - gap + i * gap
        draw.rectangle([lx, ly - lh//2, lx + lw, ly + lh//2],
                       fill=(255, 255, 255, alpha))

    return img


def save_ico(img, path):
    """Save a PIL image as .ico with multiple sizes."""
    sizes = [16, 32, 48, 64, 128, 256]
    imgs  = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    img.save(path, format="ICO", sizes=[(s, s) for s in sizes])


def main():
    assets = Path(__file__).parent / "assets"
    assets.mkdir(exist_ok=True)

    img = make_icon(256)
    png_path = assets / "icon.png"
    ico_path = assets / "icon.ico"

    img.save(png_path, format="PNG")
    save_ico(img, ico_path)

    print(f"Saved {png_path}")
    print(f"Saved {ico_path}")


if __name__ == "__main__":
    main()
