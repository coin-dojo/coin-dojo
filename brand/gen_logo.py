#!/usr/bin/env python3
"""Render the Coin Dojo logo (#258, the maintainer's pick L2, 2026-11-20): an arcade token with a reeded gold rim, GOOD FOR ONE
PLAY around the top, a torii gate over a low red sun on violet, and the COIN DOJO wordmark beside it. Drawn only from
brand/tokens.json (colours, the badge gradient, the embedded fonts). Original art (CC BY-SA 4.0). Usage: BRAND.md "Logo".

Usage: gen_logo.py [OUT_DIR]   (default brand/). Needs Pillow (~/.venvs/badges, skills/badges/SKILL.md).
Writes, all on a transparent background:
    logo.png             the lockup for DARK backgrounds (token, wordmark, tagline in `muted`)
    logo-light.png       the same lockup for LIGHT backgrounds (tagline in `dim`)
    logo-icon-512.png    the token alone; logo-icon-64.png keeps the ring text (its minimum size)
    logo-icon-32.png     the favicon: no ring text (unreadable at 32 px), a heavier gate
No SVG: the ring text and the gradient wordmark would need the fonts embedded as outlines; the PNGs are the source.
Deterministic: same tokens + same fonts + same Pillow => same bytes. Exit 0 ok, 2 no Pillow / bad usage.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brand  # noqa: E402

NAME = "COIN DOJO"
TAGLINE = "FOR ICONIC ARCADE CABINETS (RASPBERRY PI 5)"
RING = "GOOD FOR ONE PLAY"
SHEAR = 0.21              # the wordmark's slant (about 12 degrees), like the theme's carousel titles
LOCKUP_W, LOCKUP_H = 1600, 560
ICONS = [512, 64, 32]     # a list: the style lint reads a 3-number tuple as an RGB colour
RING_MIN = 64             # below this the ring text is left out (BRAND.md "Logo": minimum size)


def wordmark(b, text, width):
    """Archivo Black, sheared, filled with the badge gradient top to bottom, with a badge_outline stroke."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(b.font("display"), 300)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    stroke = 14
    l, t, r, btm = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
    w, h = r - l + 140, btm - t + 40
    mask, edge = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((70 - l, 20 - t), text, font=font, fill=255)
    ImageDraw.Draw(edge).text((70 - l, 20 - t), text, font=font, fill=255, stroke_width=stroke, stroke_fill=255)
    stops = b.stops("badge")
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        tt = min(1.0, max(0.0, (y - 20) / float(h - 40)))
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= tt <= p1:
                k = (tt - p0) / (p1 - p0) if p1 > p0 else 0.0
                px[0, y] = tuple(int(c0[i] + (c1[i] - c0[i]) * k) for i in range(3))
                break
    grad = col.resize((w, h))
    out = Image.new("RGBA", (w, h), b.rgba("bg", 0))
    out.paste(Image.new("RGBA", (w, h), b.rgba("badge_outline")), (0, 0), edge)
    out.paste(grad.convert("RGBA"), (0, 0), mask)
    out = out.transform((w + int(h * SHEAR), h), Image.AFFINE, (1, SHEAR, -h * SHEAR, 0, 1, 0), Image.BICUBIC)
    return out.resize((width, round(out.height * width / out.width)), Image.LANCZOS)


def _torii(d, cx, cy, r, colour, heavy=False):
    """A torii from plain shapes: two posts, the tie beam (nuki), the curved top beam (kasagi), the centre strut."""
    pw = r * (0.18 if heavy else 0.13)
    beam = r * (0.17 if heavy else 0.13)
    for sx in (-1, 1):
        x = cx + sx * r * 0.42
        d.polygon([(x - pw / 2, cy - r * 0.38), (x + pw / 2, cy - r * 0.38),
                   (x + pw / 2 + sx * r * 0.03, cy + r * 0.55), (x - pw / 2 + sx * r * 0.03, cy + r * 0.55)], fill=colour)
    d.rectangle([cx - r * 0.62, cy - r * 0.24, cx + r * 0.62, cy - r * 0.24 + beam * 0.85], fill=colour)
    top = []
    for i in range(41):
        t = i / 40.0
        top.append((cx - r * 0.82 + t * r * 1.64, cy - r * 0.52 - (abs(t - 0.5) * 2) ** 2 * r * 0.12))
    d.polygon(top + [(x, y + beam) for x, y in reversed(top)], fill=colour)
    d.rectangle([cx - pw * 0.35, cy - r * 0.39, cx + pw * 0.35, cy - r * 0.24], fill=colour)


def _ring_text(img, b, text, cx, cy, radius, size, sweep=200):
    """Letters along the top arc, each turned to the tangent."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(b.font("display"), size)
    n = len(text)
    for i, ch in enumerate(text):
        if ch == " ":
            continue
        ang = math.radians(-90 - sweep / 2.0 + sweep * (i + 0.5) / n)
        g = Image.new("RGBA", (size * 2, size * 2), b.rgba("bg", 0))
        ImageDraw.Draw(g).text((size, size), ch, font=font, fill=b.rgb("on_gold"), anchor="mm")
        g = g.rotate(-math.degrees(ang) - 90, resample=Image.BICUBIC)
        x, y = cx + radius * math.cos(ang), cy + radius * math.sin(ang)
        img.alpha_composite(g, (int(x - g.width / 2), int(y - g.height / 2)))


def token(b, size):
    """The token at `size` px (drawn at 1024 and scaled down); ring text only from RING_MIN up."""
    from PIL import Image, ImageDraw
    s = 1024
    ring = size >= RING_MIN
    img = Image.new("RGBA", (s, s), b.rgba("bg", 0))
    d = ImageDraw.Draw(img)
    c, r = s / 2.0, s / 2.0 - 8
    d.ellipse([c - r, c - r, c + r, c + r], fill=b.rgb("gold_card"), outline=b.rgb("badge_outline"), width=10)
    for k in range(120):                                   # the reeded edge of a real arcade token
        a = 2 * math.pi * k / 120
        d.line([(c + (r - 8) * math.cos(a), c + (r - 8) * math.sin(a)),
                (c + (r - 40) * math.cos(a), c + (r - 40) * math.sin(a))], fill=b.rgb("gold_muted"), width=5)
    ri = r - 52
    d.ellipse([c - ri, c - ri, c + ri, c + ri], fill=b.rgb("gold"), outline=b.rgb("badge_outline"), width=8)
    rf = ri - (70 if ring else 26)
    d.ellipse([c - rf, c - rf, c + rf, c + rf], fill=b.rgb("panel"), outline=b.rgb("badge_outline"), width=8)
    if ring:
        _ring_text(img, b, RING, c, c, ri - 36, 54)
        d = ImageDraw.Draw(img)
        for sx in (-1, 1):
            x = c + sx * (ri - 36) * math.cos(math.radians(30))
            d.regular_polygon((x, c + (ri - 36) * math.sin(math.radians(30)), 18), 5, rotation=-90,
                              fill=b.rgb("on_gold"))
    _torii(d, c, c + 20, rf * 0.92, b.rgb("gold"), heavy=not ring)
    d.ellipse([c - rf * 0.12, c + rf * 0.5, c + rf * 0.12, c + rf * 0.74], fill=b.rgb("crimson"))
    return img.resize((size, size), Image.LANCZOS)


def lockup(b, light):
    """Token left, wordmark right, the tagline under the wordmark; transparent background."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (LOCKUP_W, LOCKUP_H), b.rgba("bg", 0))
    t = token(b, 480)
    img.alpha_composite(t, (40, (LOCKUP_H - t.height) // 2))
    wm = wordmark(b, NAME, 1040)
    x = 40 + t.width + 20
    y = (LOCKUP_H - wm.height - 50) // 2                  # wordmark + tagline centred on the token
    img.alpha_composite(wm, (x, y))
    ImageDraw.Draw(img).text((x + 50, y + wm.height + 10), TAGLINE, font=ImageFont.truetype(b.font("ui_bold"), 34),
                             fill=b.rgb("dim" if light else "muted"), anchor="lt")
    return img


def render(out_dir, b=None):
    b = b or brand.load()
    os.makedirs(out_dir, exist_ok=True)
    files = {"logo.png": lockup(b, False), "logo-light.png": lockup(b, True)}
    for s in ICONS:
        files["logo-icon-%d.png" % s] = token(b, s)
    for name, img in sorted(files.items()):
        img.save(os.path.join(out_dir, name), optimize=True)
    return sorted(files)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: gen_logo.py [OUT_DIR]", file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow missing; use ~/.venvs/badges (skills/badges/SKILL.md)", file=sys.stderr)
        return 2
    out = argv[0] if argv else HERE
    for name in render(out):
        print("wrote %s" % os.path.join(out, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
