#!/usr/bin/env python3
"""Gamelist panel backgrounds in the brand art palette (Coin Dojo, #43): original art, CC BY-SA 4.0.

Writes panel.png (left, gold accent stripe) and panel-red.png (right, crimson-red stripe), 600x840 RGBA,
a rounded violet-black slab at 86-90% alpha with a faint lavender outline. ES-DE stretches them.
The alpha is high on purpose: list text must stay >= 4.5:1 even over the brightest art (tests/test_palette_contrast.py).
Usage: gen_panels.py [ART_DIR]   Needs Pillow. Exit 0 ok, 2 bad usage/no Pillow.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ART = os.path.normpath(os.path.join(HERE, "..", "..", "themes", "coin-dojo", "_inc", "art"))
W, H, S = 600, 840, 2
FILL = (16, 8, 34)
TOP_A, BOT_A = 0.86, 0.92       # fill alpha, top -> bottom
OUTLINE = (201, 192, 234, 80)
GOLD = (255, 201, 77, 200)
RED = (226, 59, 59, 220)


def panel(stripe, left):
    from PIL import Image, ImageDraw
    big = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    mask = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((3 * S, 3 * S, (W - 4) * S, (H - 4) * S), radius=26 * S, fill=255)
    fill = Image.new("RGBA", (1, H * S))
    for y in range(H * S):
        a = TOP_A + (BOT_A - TOP_A) * y / (H * S - 1)
        fill.putpixel((0, y), FILL + (round(255 * a),))
    fill = fill.resize((W * S, H * S))
    big.paste(fill, (0, 0), mask)
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((3 * S, 3 * S, (W - 4) * S, (H - 4) * S), radius=26 * S, outline=OUTLINE, width=2 * S)
    x0 = 3 * S if left else (W - 10) * S
    d.rounded_rectangle((x0, 3 * S, x0 + 7 * S, (H - 4) * S), radius=3 * S, fill=stripe)
    return big.resize((W, H), Image.LANCZOS)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: gen_panels.py [ART_DIR]", file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow missing; use a venv (see SKILL.md)", file=sys.stderr)
        return 2
    art = os.path.abspath(argv[0]) if argv else DEFAULT_ART
    panel(GOLD, True).save(os.path.join(art, "panel.png"))
    panel(RED, False).save(os.path.join(art, "panel-red.png"))
    print("wrote panel.png + panel-red.png in %s" % art)
    return 0


if __name__ == "__main__":
    sys.exit(main())
