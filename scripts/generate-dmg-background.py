#!/usr/bin/env python3
"""Generate the standard and Retina backgrounds used by the macOS DMG."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
WIDTH, HEIGHT = 658, 498


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    variation = {
        "regular": "Regular",
        "medium": "Medium",
        "bold": "Bold",
    }[weight]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            typeface = ImageFont.truetype(str(path), size=size)
            if path.name == "SFNS.ttf":
                typeface.set_variation_by_name(variation)
            return typeface
    return ImageFont.load_default(size=size)


def centered(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
             typeface: ImageFont.FreeTypeFont, fill: str) -> None:
    draw.text(xy, text, font=typeface, fill=fill, anchor="mm")


def make_background(scale: int) -> Image.Image:
    width, height = WIDTH * scale, HEIGHT * scale
    image = Image.new("RGB", (width, height), "#f8f4ef")
    draw = ImageDraw.Draw(image)

    # A quiet warm gradient keeps Finder labels legible while avoiding the
    # generic blank-white Electron Forge appearance.
    top = (253, 251, 248)
    bottom = (244, 235, 227)
    for y in range(height):
        amount = y / max(height - 1, 1)
        color = tuple(round(a + (b - a) * amount) for a, b in zip(top, bottom))
        draw.line((0, y, width, y), fill=color)

    header_height = 138 * scale
    header_top = (111, 49, 69)
    header_bottom = (78, 30, 47)
    for y in range(header_height):
        amount = y / max(header_height - 1, 1)
        color = tuple(
            round(a + (b - a) * amount)
            for a, b in zip(header_top, header_bottom)
        )
        draw.line((0, y, width, y), fill=color)

    image = image.convert("RGBA")
    draw = ImageDraw.Draw(image)

    # Brand mark and wordmark in the header.
    brand = Image.open(ASSETS / "icon.png").convert("RGBA")
    brand.thumbnail((62 * scale, 62 * scale), Image.Resampling.LANCZOS)
    image.alpha_composite(brand, (42 * scale, 37 * scale))
    draw.text(
        (119 * scale, 49 * scale),
        "Gnosis Images",
        font=font(27 * scale, "bold"),
        fill="#fffaf2",
        anchor="la",
    )
    draw.text(
        (120 * scale, 85 * scale),
        "Museum image search, thoughtfully ranked",
        font=font(14 * scale),
        fill="#e9cfd2",
        anchor="la",
    )

    # A restrained directional cue replaces the oversized stock black arrow.
    arrow_color = "#92556a"
    draw.rounded_rectangle(
        (292 * scale, 286 * scale, 352 * scale, 300 * scale),
        radius=7 * scale,
        fill=arrow_color,
    )
    draw.polygon(
        [
            (345 * scale, 273 * scale),
            (366 * scale, 293 * scale),
            (345 * scale, 313 * scale),
        ],
        fill=arrow_color,
    )

    centered(
        draw,
        (WIDTH // 2 * scale, 451 * scale),
        "Drag Gnosis Images to Applications to install",
        font(14 * scale, "medium"),
        "#6d5960",
    )
    return image.convert("RGB")


def main() -> None:
    outputs = ((1, "dmg-background.png"), (2, "dmg-background@2x.png"))
    for scale, filename in outputs:
        output = ASSETS / filename
        make_background(scale).save(output, optimize=True)
        print(f"Wrote {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
