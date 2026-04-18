from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont


TARGET_RATIO = 12 / 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a website banner from an input image."
    )
    parser.add_argument("input_image_path", help="Path to the source image")
    parser.add_argument(
        "--output_image_path",
        default="banner.png",
        help="Path to save output PNG (default: banner.png)",
    )
    parser.add_argument(
        "--crop",
        default="center",
        help="Crop mode: center, top, bottom, or integer top offset in pixels",
    )
    parser.add_argument(
        "--gradient_width",
        type=int,
        default=66,
        help="Gradient width as percent of banner width (0-100)",
    )
    parser.add_argument(
        "--gradient_color",
        default="white",
        help="Gradient color (#RRGGBB, #RRGGBBAA, or named color)",
    )
    parser.add_argument(
        "--text",
        default="",
        help="Optional text to overlay on the banner",
    )
    parser.add_argument(
        "--font",
        default="Noto Sans",
        help="Installed system font family name (default: Noto Sans)",
    )
    return parser.parse_args()


def parse_crop_mode(crop_value: str) -> str | int:
    mode = crop_value.strip().lower()
    if mode in {"center", "top", "bottom"}:
        return mode

    try:
        return int(crop_value)
    except ValueError as exc:
        raise ValueError(
            "Invalid crop value. Use center, top, bottom, or an integer top offset."
        ) from exc


def parse_gradient_color(color_value: str) -> tuple[int, int, int, int]:
    value = color_value.strip()
    if value.startswith("#"):
        hex_value = value[1:]
        if len(hex_value) == 6:
            value = f"#{hex_value}FF"
        elif len(hex_value) != 8:
            raise ValueError("Gradient color must be #RRGGBB or #RRGGBBAA.")

    try:
        parsed = ImageColor.getcolor(value, "RGBA")
    except ValueError as exc:
        raise ValueError(
            "Invalid gradient color. Use #RRGGBB, #RRGGBBAA, or a named color."
        ) from exc

    if not isinstance(parsed, tuple):
        raise ValueError("Gradient color must resolve to an RGBA tuple.")
    if len(parsed) == 3:
        return (parsed[0], parsed[1], parsed[2], 255)
    if len(parsed) == 4:
        return (parsed[0], parsed[1], parsed[2], parsed[3])
    raise ValueError("Gradient color must resolve to RGB or RGBA.")


def compute_crop_box(
    width: int, height: int, crop_mode: str | int, target_ratio: float
) -> tuple[int, int, int, int]:
    image_ratio = width / height

    if image_ratio < target_ratio:
        # Image is too tall for target ratio; crop height based on mode.
        crop_height = int(round(width / target_ratio))
        if crop_height <= 0 or crop_height > height:
            raise ValueError("Could not compute a valid crop height for target ratio 10:3.")

        if isinstance(crop_mode, int):
            top = crop_mode
        elif crop_mode == "top":
            top = 0
        elif crop_mode == "bottom":
            top = height - crop_height
        else:
            top = (height - crop_height) // 2

        max_top = height - crop_height
        if top < 0 or top > max_top:
            raise ValueError(
                f"Crop offset {top} is out of range for image height {height}. "
                f"Valid range is 0 to {max_top}."
            )

        return (0, top, width, top + crop_height)

    # Image is too wide (or exactly ratio); crop width centered and keep full height.
    crop_width = int(round(height * target_ratio))
    if crop_width <= 0 or crop_width > width:
        raise ValueError("Could not compute a valid crop width for target ratio 10:3.")

    left = (width - crop_width) // 2
    return (left, 0, left + crop_width, height)


def apply_gradient_overlay(
    image: Image.Image,
    gradient_color: tuple[int, int, int, int],
    gradient_width_percent: int,
) -> Image.Image:
    width, height = image.size
    gradient_width_px = int(round(width * gradient_width_percent / 100.0))

    if gradient_width_px <= 0:
        return image

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    base_r, base_g, base_b, base_a = gradient_color
    gradient_width_px = min(gradient_width_px, width)

    for x in range(gradient_width_px):
        if gradient_width_px == 1:
            alpha_scale = 1.0
        else:
            alpha_scale = 1.0 - (x / (gradient_width_px - 1))

        alpha = int(round(base_a * alpha_scale))
        if alpha <= 0:
            continue

        for y in range(height):
            overlay.putpixel((x, y), (base_r, base_g, base_b, alpha))

    return Image.alpha_composite(image, overlay)


def _srgb_to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    r_lin = _srgb_to_linear(r / 255.0)
    g_lin = _srgb_to_linear(g / 255.0)
    b_lin = _srgb_to_linear(b / 255.0)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int]) -> float:
    lum_a = relative_luminance(rgb_a)
    lum_b = relative_luminance(rgb_b)
    lighter = max(lum_a, lum_b)
    darker = min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def choose_black_or_white_text_color(
    gradient_color: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    bg = gradient_color[:3]
    black = (0, 0, 0)
    white = (255, 255, 255)

    black_contrast = contrast_ratio(bg, black)
    white_contrast = contrast_ratio(bg, white)

    if black_contrast >= white_contrast:
        return (0, 0, 0, 255)
    return (255, 255, 255, 255)


def find_system_font_path(font_name: str) -> Path:
    candidate = Path(font_name)
    if candidate.is_file():
        return candidate

    try:
        result = subprocess.run(
            ["fc-list", "--format=%{family}\t%{file}\n"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "Font lookup requires fontconfig (fc-list), but it is not installed."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError("Failed to query system fonts using fc-list.") from exc

    target = font_name.casefold()
    fallback_path: Path | None = None

    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        family_part, file_part = line.split("\t", 1)
        font_path = Path(file_part.strip())
        if not font_path.is_file():
            continue

        families = [name.strip().casefold() for name in family_part.split(",") if name]
        if target in families:
            return font_path
        if fallback_path is None and any(target in fam for fam in families):
            fallback_path = font_path

    if fallback_path is not None:
        return fallback_path

    raise ValueError(
        f"Font '{font_name}' was not found. Please provide an installed system font name."
    )


def draw_text_overlay(
    image: Image.Image,
    text: str,
    font_name: str,
    gradient_color: tuple[int, int, int, int],
    gradient_width_percent: int,
) -> Image.Image:
    if not text:
        return image

    width, height = image.size
    margin = max(1, int(round(height * 0.10)))
    max_text_height = max(1, height - (2 * margin))

    gradient_limit = int(round(width * max(gradient_width_percent / 100.0, 0.20)))
    max_text_width = max(1, min(width - margin, gradient_limit - margin) - margin)

    font_path = find_system_font_path(font_name)
    draw = ImageDraw.Draw(image)
    text_color = choose_black_or_white_text_color(gradient_color)

    min_size = 8
    low = min_size
    high = max(16, max_text_height * 2)
    chosen_font: ImageFont.FreeTypeFont | None = None
    chosen_bbox: tuple[float, float, float, float] | None = None

    while low <= high:
        size = (low + high) // 2
        font = ImageFont.truetype(str(font_path), size=size)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        if text_width <= max_text_width and text_height <= max_text_height:
            chosen_font = font
            chosen_bbox = bbox
            low = size + 1
        else:
            high = size - 1

    if chosen_font is None or chosen_bbox is None:
        chosen_font = ImageFont.truetype(str(font_path), size=min_size)
        chosen_bbox = draw.textbbox((0, 0), text, font=chosen_font)

    if chosen_bbox is None:
        raise ValueError("Failed to compute text bounds.")

    text_height = int(round(chosen_bbox[3] - chosen_bbox[1]))
    x = margin

    # Center using glyph bounds, then clamp to the requested 10% top/bottom margins.
    target_center_y = height / 2.0
    glyph_center_offset = (chosen_bbox[1] + chosen_bbox[3]) / 2.0
    y = int(round(target_center_y - glyph_center_offset))

    top = y + chosen_bbox[1]
    bottom = y + chosen_bbox[3]
    if top < margin:
        y += int(round(margin - top))
    elif bottom > (height - margin):
        y -= int(round(bottom - (height - margin)))

    if text_color[0] == 255:
        stroke_color = (0, 0, 0, 128)
    else:
        stroke_color = (255, 255, 255, 128)
    draw.text((x, y), text, fill=text_color, font=chosen_font, stroke_width=5, stroke_fill=stroke_color)
    return image


def build_banner(args: argparse.Namespace) -> Image.Image:
    if args.gradient_width < 0 or args.gradient_width > 100:
        raise ValueError("gradient_width must be between 0 and 100.")

    crop_mode = parse_crop_mode(args.crop)
    gradient_color = parse_gradient_color(args.gradient_color)

    input_path = Path(args.input_image_path)
    if not input_path.is_file():
        raise ValueError(f"Input image not found: {input_path}")

    with Image.open(input_path) as source_image:
        image = source_image.convert("RGBA")

    crop_box = compute_crop_box(image.width, image.height, crop_mode, TARGET_RATIO)
    image = image.crop(crop_box)
    image = apply_gradient_overlay(image, gradient_color, args.gradient_width)

    if args.text.strip():
        image = draw_text_overlay(
            image,
            text=args.text,
            font_name=args.font,
            gradient_color=gradient_color,
            gradient_width_percent=args.gradient_width,
        )

    return image


def main() -> int:
    args = parse_args()

    try:
        banner = build_banner(args)
        output_path = Path(args.output_image_path)
        banner.save(output_path, format="PNG")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
