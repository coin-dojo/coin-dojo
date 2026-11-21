#!/usr/bin/env python3
"""Original gradient scrims that sit between the backdrop art and the UI (Coin Dojo, #43).

Writes two 8x1080 vertical-gradient PNGs (violet-black, alpha varies with height; ES-DE stretches them):
  scrim.png       system view: clear at the top (the art shows), ~38% behind the carousel, ~85% at the footer
  scrim-list.png  gamelist view: a flat ~45% veil (the art stays visible around the panels), ~85% at the footer
Deterministic: same code => same bytes.  Usage: gen_scrim.py [ART_DIR]  (default theme _inc/art)
Needs Pillow (venv, see SKILL.md). Exit: 0 ok, 2 bad usage or Pillow missing.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ART = os.path.normpath(os.path.join(HERE, "..", "..", "themes", "coin-dojo", "_inc", "art"))
H, W = 1080, 8
COLOR = (14, 8, 34)                        # violet-black, same family as the panels and the deck
# (y fraction, alpha 0..1) control points, linear in between
SYSTEM = [(0.0, 0.0), (0.18, 0.0), (0.34, 0.30), (0.42, 0.38), (0.70, 0.40), (0.86, 0.72), (1.0, 0.88)]
LIST = [(0.0, 0.40), (0.80, 0.50), (0.86, 0.72), (1.0, 0.88)]


def alpha_at(stops, f):
    for (f0, a0), (f1, a1) in zip(stops, stops[1:]):
        if f0 <= f <= f1:
            return a0 + (a1 - a0) * (f - f0) / ((f1 - f0) or 1)
    return stops[-1][1]


def scrim_rows(stops):
    return [round(255 * alpha_at(stops, y / (H - 1))) for y in range(H)]


def write(path, stops):
    from PIL import Image
    im = Image.new("RGBA", (W, H))
    for y, a in enumerate(scrim_rows(stops)):
        for x in range(W):
            im.putpixel((x, y), COLOR + (a,))
    im.save(path)
    print("wrote %s %dx%d" % (path, W, H))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: gen_scrim.py [ART_DIR]", file=sys.stderr)
        return 2
    art = os.path.abspath(argv[0]) if argv else DEFAULT_ART
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow missing; use a venv (see SKILL.md)", file=sys.stderr)
        return 2
    os.makedirs(art, exist_ok=True)
    write(os.path.join(art, "scrim.png"), SYSTEM)
    write(os.path.join(art, "scrim-list.png"), LIST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
