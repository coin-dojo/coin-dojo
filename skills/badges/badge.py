#!/usr/bin/env python3
"""Make Coin Dojo-style chrome carousel badges.

Strong italic Archivo Black, cream -> gold -> sage -> teal vertical gradient
(sampled from the original badges), navy outline + soft white glow, transparent RGBA, normalised to 256 px tall.
Deterministic: same text + same font file => byte-identical PNG.

Usage:   badge.py [--font TTF] OUT TEXT [OUT TEXT ...]
Example: badge.py must-play.png "MUST PLAY" 1990-94.png 1990-94

Only dependency: Pillow (use a venv, see SKILL.md). Exit codes: 0 ok, 2 bad usage
or missing font, 1 render failure.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FONT = os.path.normpath(os.path.join(
    HERE, "..", "..", "themes", "coin-dojo", "_inc", "fonts",
    "ArchivoBlack-Regular.ttf"))

FS, STROKE, SHEAR, PAD, HEIGHT = 300, 10, 0.23, 60, 256
INK = 0.795
NAVY = (9, 22, 58, 255)
# Stops sampled from the hand-made originals (fighters.png, 1990s.png), mapped
# over the glyph's own height: cream -> gold at mid -> sage -> teal.
STOPS = [(0.00, (252, 239, 195)), (0.25, (253, 224, 140)), (0.50, (253, 182, 67)),
         (0.70, (189, 189, 114)), (0.90, (115, 202, 177)), (1.00, (61, 166, 174))]


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _color_at(f):
    for i in range(len(STOPS) - 1):
        y0, c0 = STOPS[i]
        y1, c1 = STOPS[i + 1]
        if y0 <= f <= y1:
            return _lerp(c0, c1, (f - y0) / ((y1 - y0) or 1))
    return STOPS[-1][1]


def make(text, out, font_path):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    font = ImageFont.truetype(font_path, FS)
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bb = d.textbbox((0, 0), text, font=font, stroke_width=STROKE)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    W = tw + PAD * 2 + int((th + PAD) * SHEAR)
    H = th + PAD * 2
    tx, ty = PAD - bb[0], PAD - bb[1]
    sil = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sil).text((tx, ty), text, font=font, fill=255,
                             stroke_width=STROKE, stroke_fill=255)
    gly = Image.new("L", (W, H), 0)
    ImageDraw.Draw(gly).text((tx, ty), text, font=font, fill=255)
    gy0, gy1 = gly.getbbox()[1], gly.getbbox()[3] - 1
    col = Image.new("RGBA", (1, H))
    for y in range(H):
        f = min(1.0, max(0.0, (y - gy0) / ((gy1 - gy0) or 1)))
        col.putpixel((0, y), _color_at(f) + (255,))
    grad = col.resize((W, H), Image.NEAREST)
    o = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow.paste((255, 255, 255, 255), (0, 0), sil)
    glow = glow.filter(ImageFilter.GaussianBlur(9))
    o.alpha_composite(glow)
    o.alpha_composite(glow)
    o.paste(Image.new("RGBA", (W, H), NAVY), (0, 0), sil)
    o.paste(grad, (0, 0), gly)
    s = o.transform((W, H), Image.AFFINE, (1, SHEAR, -SHEAR * H, 0, 1, 0),
                    resample=Image.BICUBIC)
    s = s.crop(s.getbbox())
    w, h = s.size
    # Originals keep the letters to rows ~50..205 of 256; scale the inked
    # crop to INK of the height and centre it so new badges sit the same size.
    ih = round(HEIGHT * INK)
    s = s.resize((round(w * ih / h), ih), Image.LANCZOS)
    c = Image.new("RGBA", (s.width, HEIGHT), (0, 0, 0, 0))
    c.alpha_composite(s, (0, (HEIGHT - ih) // 2))
    c.save(out)
    return c.size


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--font", default=DEFAULT_FONT, help="TTF path (default: theme's Archivo Black)")
    ap.add_argument("pairs", nargs="+", metavar="OUT TEXT")
    a = ap.parse_args(argv)
    if len(a.pairs) % 2:
        print("error: arguments must be OUT TEXT pairs", file=sys.stderr)
        return 2
    if not os.path.isfile(a.font):
        print("error: font not found: %s" % a.font, file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow missing; use a venv (see skills/badges/SKILL.md)", file=sys.stderr)
        return 2
    for out, text in zip(a.pairs[0::2], a.pairs[1::2]):
        try:
            size = make(text, out, a.font)
        except Exception as e:  # noqa: BLE001
            print("error: %s: %s" % (out, e), file=sys.stderr)
            return 1
        print("wrote %s %dx%d" % (out, size[0], size[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
