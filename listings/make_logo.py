"""Render the 400x400 PNG icon DOS submits to icon-bearing listing venues.

The artifact (logo.png) is the thing the Cline MCP marketplace issue carries; this
script is its REPRODUCIBLE source of truth (the verify-action/ / gitlab-ci/ pattern:
the artifact lives in-tree, the generator next to it). No network, no SVG-renderer
dependency — pure Pillow, so a `python make_logo.py` regenerates a byte-stable icon
on any box.

Brand: the same two colours as the shipped badge (docs/assets/verified-by-dos.svg) —
GitHub-dark `#24292e` ground, the `#2ea44f` ship-green check — under a "DOS" wordmark.
The motif is a checkmark inside a rounded square: the kernel's one job, a verified
ship-claim, not the agent's word.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 400
DARK = (36, 41, 46)       # #24292e — GitHub dark, the badge's left half
GREEN = (46, 164, 79)     # #2ea44f — ship-green, the badge's check
WHITE = (255, 255, 255)


def _font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(out: Path) -> Path:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square ground — the icon tile.
    d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=72, fill=DARK)

    # The green checkmark — three points, a thick ground-truth tick, upper area.
    cx, cy = SIZE / 2, 168
    d.line(
        [(cx - 78, cy + 6), (cx - 22, cy + 60), (cx + 88, cy - 62)],
        fill=GREEN,
        width=34,
        joint="curve",
    )
    # Round the stroke ends so the tick reads clean at small sizes.
    for px, py in [(cx - 78, cy + 6), (cx + 88, cy - 62)]:
        d.ellipse([px - 17, py - 17, px + 17, py + 17], fill=GREEN)

    # The "DOS" wordmark.
    font = _font(
        [
            "/c/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "arialbd.ttf",
            "DejaVuSans-Bold.ttf",
        ],
        110,
    )
    text = "DOS"
    tb = d.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.text(((SIZE - tw) / 2 - tb[0], 250 - tb[1]), text, font=font, fill=WHITE)

    img.save(out, "PNG")
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("logo.png")
    p = render(target)
    with Image.open(p) as im:
        print(f"wrote {p} ({im.size[0]}x{im.size[1]} {im.mode})")
