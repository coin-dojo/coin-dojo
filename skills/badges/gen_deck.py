#!/usr/bin/env python3
"""Translucent control-deck strip for the footer (Coin Dojo, #43): original art, CC BY-SA 4.0.

2880x340 RGBA (shown at 1x0.14 of the screen). A soft violet-black gradient that is clear at the top
and ~60% at the bottom (the screen-wide scrim adds the rest), a faint diagonal speckle, and a gold hairline
on the top edge that fades out toward both sides. The art behind shows through. Deterministic.
Usage: gen_deck.py [OUT.png]   (default theme _inc/art/deck.png). Needs Pillow. Exit 0 ok, 2 bad usage/no Pillow.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.normpath(os.path.join(HERE, "..", "..", "themes", "coin-dojo",
                                            "_inc", "art", "deck.png"))
W, H = 2880, 340
COLOR = (14, 8, 34)
GOLD = (255, 201, 77)


def deck_alpha(y):
    """Fill alpha 0..255 at row y: 0 at the top edge, 60% at the bottom."""
    return round(255 * 0.60 * (y / (H - 1)) ** 0.8)


def hairline_alpha(x):
    """Gold hairline: 70% in the middle, fading to 0 at both ends."""
    t = abs(x - (W - 1) / 2) / ((W - 1) / 2)
    return round(255 * 0.70 * (1 - t ** 3))


def build():
    from PIL import Image
    im = Image.new("RGBA", (W, H))
    px = im.load()
    for y in range(H):
        a = deck_alpha(y)
        for x in range(W):
            px[x, y] = COLOR + (a,)
    # diagonal speckle lines (28 px grid at 2x), a few percent alpha, lavender
    for k in range(-H, W, 56):
        for y in range(H):
            x = k + y
            if 0 <= x < W:
                r, g, b, a = px[x, y]
                px[x, y] = (min(255, r + 40), min(255, g + 34), min(255, b + 70), min(255, a + 14))
    for x in range(W):
        for y in range(3):
            px[x, y] = GOLD + (hairline_alpha(x) // (1 + y),)
    return im


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: gen_deck.py [OUT.png]", file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow missing; use a venv (see SKILL.md)", file=sys.stderr)
        return 2
    out = os.path.abspath(argv[0]) if argv else DEFAULT_OUT
    build().save(out)
    print("wrote %s %dx%d" % (out, W, H))
    return 0


if __name__ == "__main__":
    sys.exit(main())
