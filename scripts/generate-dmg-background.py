#!/usr/bin/env python3
"""Generate the standard and Retina backgrounds used by the macOS DMG."""

from pathlib import Path
import re
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
WIDTH, HEIGHT = 658, 498
BANNER_TEXT_SCALE = 1.5


def banner_text_size(size: int, scale: int) -> int:
    """Return a pixel size 150% larger for standard and Retina artwork."""
    return round(size * BANNER_TEXT_SCALE * scale)


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


def heading_font(size: int) -> ImageFont.FreeTypeFont:
    """Return the same sturdy Georgia face used by the app's hero heading."""
    path = Path("/System/Library/Fonts/Supplemental/Georgia.ttf")
    if path.exists():
        return ImageFont.truetype(str(path), size=size)
    return font(size, "regular")


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

    # The app icon already appears in Finder below, so keep this banner focused
    # on a vertically centered wordmark and descriptor.
    title_typeface = heading_font(banner_text_size(31, scale))
    subtitle_typeface = font(banner_text_size(14, scale))
    title = "Gnosis Images"
    subtitle = "Advanced creative commons image search"
    gap = 7 * scale
    title_box = draw.textbbox((0, 0), title, font=title_typeface, anchor="lt")
    subtitle_box = draw.textbbox(
        (0, 0), subtitle, font=subtitle_typeface, anchor="lt"
    )
    title_height = title_box[3] - title_box[1]
    subtitle_height = subtitle_box[3] - subtitle_box[1]
    group_height = title_height + gap + subtitle_height
    group_top = (header_height - group_height) // 2
    left = 52 * scale
    draw.text(
        (left, group_top),
        title,
        font=title_typeface,
        fill="#f8e8c4",
        anchor="lt",
    )
    draw.text(
        (left, group_top + title_height + gap),
        subtitle,
        font=subtitle_typeface,
        fill="#f8e8c4",
        anchor="lt",
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


def svg_path_polygons(path: Path, curve_steps: int = 24) -> list[list[tuple[float, float]]]:
    """Flatten the M/L/C/Z commands in the sourced Applications SVG path."""
    root = ET.parse(path).getroot()
    path_element = next(element for element in root if element.tag.endswith("path"))
    tokens = re.findall(
        r"[MLCZ]|-?(?:\d+(?:\.\d*)?|\.\d+)", path_element.attrib["d"]
    )
    polygons: list[list[tuple[float, float]]] = []
    points: list[tuple[float, float]] = []
    current = (0.0, 0.0)
    index = 0

    while index < len(tokens):
        command = tokens[index]
        index += 1
        if command == "M":
            if points:
                polygons.append(points)
            current = (float(tokens[index]), float(tokens[index + 1]))
            index += 2
            points = [current]
        elif command == "L":
            current = (float(tokens[index]), float(tokens[index + 1]))
            index += 2
            points.append(current)
        elif command == "C":
            control_1 = (float(tokens[index]), float(tokens[index + 1]))
            control_2 = (float(tokens[index + 2]), float(tokens[index + 3]))
            end = (float(tokens[index + 4]), float(tokens[index + 5]))
            index += 6
            start = current
            for step in range(1, curve_steps + 1):
                t = step / curve_steps
                inverse = 1 - t
                points.append((
                    inverse ** 3 * start[0]
                    + 3 * inverse ** 2 * t * control_1[0]
                    + 3 * inverse * t ** 2 * control_2[0]
                    + t ** 3 * end[0],
                    inverse ** 3 * start[1]
                    + 3 * inverse ** 2 * t * control_1[1]
                    + 3 * inverse * t ** 2 * control_2[1]
                    + t ** 3 * end[1],
                ))
            current = end
        elif command == "Z":
            polygons.append(points)
            points = []
        else:
            raise ValueError(f"Unsupported SVG command: {command}")

    if points:
        polygons.append(points)
    return polygons


def make_applications_folder_icon() -> Image.Image:
    """Create a burgundy Applications folder that belongs with the app icon."""
    size = 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (126, 288, 898, 826), radius=44, fill=(49, 19, 30, 105)
    )
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(30)), (0, 18))

    draw = ImageDraw.Draw(image)
    # Back shell and tab.
    draw.rounded_rectangle(
        (126, 216, 898, 744), radius=38, fill="#8f5065",
        outline="#a86b7e", width=8
    )
    draw.rounded_rectangle((190, 180, 474, 336), radius=30, fill="#8f5065")
    draw.polygon([(395, 180), (488, 264), (395, 318)], fill="#8f5065")

    # A warm inner sheet gives the same cream-on-burgundy contrast as the
    # caduceus while retaining the familiar Finder folder silhouette.
    draw.rounded_rectangle((134, 252, 890, 684), radius=27, fill="#f5dfb7")

    front_mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(front_mask)
    mask_draw.rounded_rectangle((126, 288, 898, 826), radius=44, fill=255)
    front = Image.new("RGBA", image.size, (0, 0, 0, 0))
    front_draw = ImageDraw.Draw(front)
    for y in range(288, 827):
        amount = (y - 288) / (826 - 288)
        top = (127, 58, 81)
        bottom = (83, 31, 49)
        color = tuple(round(a + (b - a) * amount) for a, b in zip(top, bottom))
        front_draw.line((126, y, 898, y), fill=(*color, 255))
    transparent = Image.new("RGBA", image.size)
    image.alpha_composite(Image.composite(front, transparent, front_mask))

    # Use Apple's actual App Store / Applications glyph geometry. The system
    # folder renders it as a shallow inset, with a soft lower-right shadow and
    # a fine upper-left highlight around the darker face.
    glyph_box = (355, 438, 669, 714)
    glyph_scale_x = (glyph_box[2] - glyph_box[0]) / 150
    glyph_scale_y = (glyph_box[3] - glyph_box[1]) / 132
    glyph_mask = Image.new("L", image.size, 0)
    glyph_draw = ImageDraw.Draw(glyph_mask)
    for polygon in svg_path_polygons(ASSETS / "macos-applications-glyph.svg"):
        transformed = [
            (glyph_box[0] + x * glyph_scale_x, glyph_box[1] + y * glyph_scale_y)
            for x, y in polygon
        ]
        glyph_draw.polygon(transformed, fill=255)

    soft_shadow_mask = Image.new("L", image.size, 0)
    soft_shadow_mask.paste(glyph_mask.filter(ImageFilter.GaussianBlur(5)), (0, 5))
    soft_shadow_mask = soft_shadow_mask.point(lambda alpha: round(alpha * 0.42))
    soft_shadow = Image.new("RGBA", image.size, "#2c0d19")
    soft_shadow.putalpha(soft_shadow_mask)
    image.alpha_composite(soft_shadow)

    glyph_face = Image.new("RGBA", image.size, "#461a2a")
    glyph_face.putalpha(glyph_mask)
    image.alpha_composite(glyph_face)

    shifted_down_right = Image.new("L", image.size, 0)
    shifted_down_right.paste(glyph_mask, (3, 3))
    highlight_mask = ImageChops.subtract(glyph_mask, shifted_down_right)
    highlight_mask = highlight_mask.point(lambda alpha: round(alpha * 0.48))
    highlight = Image.new("RGBA", image.size, "#7a3b50")
    highlight.putalpha(highlight_mask)
    image.alpha_composite(highlight)

    shifted_up_left = Image.new("L", image.size, 0)
    shifted_up_left.paste(glyph_mask, (-3, -3))
    bevel_shadow_mask = ImageChops.subtract(glyph_mask, shifted_up_left)
    bevel_shadow_mask = bevel_shadow_mask.point(lambda alpha: round(alpha * 0.58))
    bevel_shadow = Image.new("RGBA", image.size, "#2d0c1a")
    bevel_shadow.putalpha(bevel_shadow_mask)
    image.alpha_composite(bevel_shadow)

    return image


def main() -> None:
    outputs = ((1, "dmg-background.png"), (2, "dmg-background@2x.png"))
    for scale, filename in outputs:
        output = ASSETS / filename
        make_background(scale).save(output, optimize=True)
        print(f"Wrote {output.relative_to(ROOT)}")

    folder_output = ASSETS / "applications-folder.png"
    make_applications_folder_icon().save(folder_output, optimize=True)
    print(f"Wrote {folder_output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
