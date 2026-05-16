#!/usr/bin/env python3
"""
Download IBM VGA 8x16 font for local development.
Run once from the repo root: python tools/download_fonts.py

The font is from VileR's "Oldschool PC Font Resource" (int10h.org)
Licensed under CC BY-SA 4.0.
"""

import urllib.request
import zipfile
import os
import io

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")

# The complete oldschool PC font pack from int10h.org
FONT_ZIP_URL = "https://int10h.org/oldschool-pc-fonts/download/oldschool_pc_font_pack_v2.2_FULL.zip"
TARGET_FONT  = "Px437_IBM_VGA_8x16.ttf"


def main():
    os.makedirs(FONT_DIR, exist_ok=True)
    target = os.path.join(FONT_DIR, TARGET_FONT)

    if os.path.exists(target):
        print(f"Font already exists: {target}")
        return

    print(f"Downloading font pack from int10h.org...")
    try:
        with urllib.request.urlopen(FONT_ZIP_URL, timeout=30) as resp:
            data = resp.read()
    except Exception as e:
        print(f"Download failed: {e}")
        print("ScrollReader will fall back to Courier New.")
        return

    print("Extracting...")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if TARGET_FONT in name:
                with z.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                print(f"Saved: {target}")
                return

    print(f"Could not find {TARGET_FONT} in the font pack.")
    print("ScrollReader will fall back to Courier New.")


if __name__ == "__main__":
    main()
