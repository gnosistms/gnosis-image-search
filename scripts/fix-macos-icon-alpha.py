#!/usr/bin/env python3
"""Restore the transparent silhouette defined by assets/icon.svg.

The checked-in PNG was previously flattened onto white.  This script applies
the SVG's 900 px rounded rectangle (x/y 62, radius 218) as a supersampled alpha
mask while leaving all fully covered artwork pixels unchanged.
"""

from pathlib import Path
import subprocess

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "assets" / "icon.png"
PNG2ICONS = ROOT / "node_modules" / ".bin" / "png2icons"
SCALE = 8


def main() -> None:
    icon = Image.open(ICON).convert("RGBA")
    if icon.size != (1024, 1024):
        raise SystemExit(f"Expected a 1024x1024 icon, got {icon.size}")

    mask_large = Image.new("L", (1024 * SCALE, 1024 * SCALE), 0)
    draw = ImageDraw.Draw(mask_large)
    draw.rounded_rectangle(
        (62 * SCALE, 62 * SCALE, 962 * SCALE, 962 * SCALE),
        radius=218 * SCALE,
        fill=255,
    )
    mask = mask_large.resize(icon.size, Image.Resampling.LANCZOS)

    red, green, blue, _ = icon.split()
    Image.merge("RGBA", (red, green, blue, mask)).save(ICON, optimize=True)

    if not PNG2ICONS.exists():
        raise SystemExit("Run npm install before rebuilding assets/icon.icns")
    subprocess.run(
        [str(PNG2ICONS), str(ICON), str(ROOT / "assets" / "icon"), "-icns", "-bc"],
        check=True,
    )


if __name__ == "__main__":
    main()
