from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw


def _is_background(pixel: tuple[int, int, int, int]) -> bool:
    red, green, blue, _alpha = pixel
    return min(red, green, blue) >= 215 and max(red, green, blue) - min(red, green, blue) <= 20


def remove_edge_background(image: Image.Image) -> Image.Image:
    original = image.convert("RGBA")
    rgba = original.copy()
    pixels = rgba.load()
    width, height = rgba.size
    queue: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()

    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited or not _is_background(pixels[x, y]):
            continue
        visited.add((x, y))
        pixels[x, y] = (*pixels[x, y][:3], 0)
        if x:
            queue.append((x - 1, y))
        if x + 1 < width:
            queue.append((x + 1, y))
        if y:
            queue.append((x, y - 1))
        if y + 1 < height:
            queue.append((x, y + 1))

    # Clear checkerboard trapped inside wordmark counters while preserving
    # the white document and checkmark inside the emblem at the left.
    for y in range(height):
        for x in range(410, width):
            if _is_background(pixels[x, y]):
                pixels[x, y] = (*pixels[x, y][:3], 0)

    # The source's white document is visually enclosed but connected to the
    # checkerboard through anti-aliased edge pixels. Restore only its bounded
    # interior, which is later obscured naturally by the foreground folder.
    paper_mask = Image.new("L", rgba.size, 0)
    ImageDraw.Draw(paper_mask).polygon(
        [(129, 437), (283, 437), (342, 496), (342, 542), (313, 542), (285, 514), (129, 514)],
        fill=255,
    )
    rgba.alpha_composite(Image.composite(original, Image.new("RGBA", rgba.size), paper_mask))
    return rgba


def padded_crop(image: Image.Image, *, padding: int) -> Image.Image:
    alpha_bbox = image.getchannel("A").getbbox()
    if alpha_bbox is None:
        raise ValueError("The source image contains no visible logo pixels.")
    left, top, right, bottom = alpha_bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    return image.crop((left, top, right, bottom))


def square_icon(image: Image.Image, *, size: int = 512) -> Image.Image:
    emblem = image.crop((0, 0, min(420, image.width), image.height))
    emblem = padded_crop(emblem, padding=6)
    available = int(size * 0.86)
    scale = min(available / emblem.width, available / emblem.height)
    resized = emblem.resize(
        (max(1, round(emblem.width * scale)), max(1, round(emblem.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(
        resized,
        ((size - resized.width) // 2, (size - resized.height) // 2),
    )
    return canvas


def build_assets(source: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned = remove_edge_background(Image.open(source))
    wordmark = padded_crop(cleaned, padding=18)
    icon = square_icon(cleaned)

    wordmark.save(output_dir / "docmarshal-wordmark.png", optimize=True)
    icon.save(output_dir / "docmarshal-icon.png", optimize=True)
    icon.save(
        output_dir / "docmarshal.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build transparent DocMarshal desktop brand assets.")
    parser.add_argument("source", type=Path, help="Source DocMarshal logo image")
    parser.add_argument("--output-dir", type=Path, default=Path("assets"))
    arguments = parser.parse_args()
    build_assets(arguments.source, arguments.output_dir)
