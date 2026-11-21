#!/usr/bin/env python3
"""Generate the system-view category chips and genre pictograms for Coin Dojo.

For every carousel entry (`_inc/systems/<name>.png`, one per collection/system) this writes
  _inc/chips/<name>.png   small pill: CURATED / DECADE / GENRE / SYSTEM (per-entry file, because
                          ES-DE themes have no per-item variables, only the ${system.theme} path)
  _inc/icons/<name>.png   original geometric pictogram, only for the GENRE entries
plus `_inc/chips/default.png` (SYSTEM) and `_inc/icons/default.png` (fully transparent) used as
the `<default>` for entries without a file of their own.

All art is original, drawn with Pillow primitives (no copied icons, no game art). CC BY-SA 4.0.
Deterministic: same inputs => same bytes.

Usage:   gen_chips.py [THEME_DIR]      (default: themes/coin-dojo)
Only dependency: Pillow (venv, see SKILL.md). Exit: 0 ok, 2 bad usage / missing font or Pillow, 1 failure.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_THEME = os.path.normpath(os.path.join(HERE, "..", "..", "themes", "coin-dojo"))
# Colours, fonts and the badge spec come from the style kit (brand/tokens.json, brand/BRAND.md).
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "brand")))
import brand as _brand  # noqa: E402
BRAND = _brand.load()

# category -> (label, accent colour). Order of CATEGORIES is the intended carousel order.
CATEGORIES = {c: (v["label"], BRAND.rgb(v["color"])) for c, v in BRAND["chips"].items()}
MEMBERS = {
    # the nested main screen (#38): MUST PLAY is the curated list, the other three are browse doors
    "browse": ["genres", "series", "decades", "systems"],
    # the SETTINGS app (#34): last on the carousel, a tool, not games
    "setup": ["trophies", "settings"],
    # QUIT TO MAIN MENU (#214, art #218): leaves ES-DE for the boot menu; its own chip + an exit pictogram
    "exit": ["quit"],
    # no auto-favorites: favorites are off on the cabinet (7c2a182), that collection never shows
    "curated": ["must-play", "must-play-genres", "arcade-classics", "auto-allgames", "auto-lastplayed",
               "custom-collections"],
    "decade": ["1980-84", "1985-89", "1990-94", "1995-99", "2000-04",
               "1980s", "1990s", "2000s", "2010s"],
    "genre": ["fighters", "beat-em-up", "shmups", "platformers", "puzzle", "racing",
              "sports", "run-and-gun", "twin-stick", "party", "maze"],
    "system": ["cps1", "cps2", "cps3", "fbneo", "neogeo", "dreamcast", "n64", "nes", "snes",
               "gb", "gbc", "gba", "genesis", "pcengine", "psx", "saturn",
               # #296: every /data/roms folder has a badge + chip (arcade = MAME)
               "segacd", "atari2600", "arcade", "naomi", "psp"],
}
ICONS = MEMBERS["genre"] + MEMBERS["exit"]   # entries with a pictogram above the chip
NAVY = BRAND.rgb("navy")   # violet-black: chip fill + icon outline (brand palette, #43)
STOPS = BRAND.stops("pictogram")
CHIP_ALPHA = BRAND["alpha"]["chip_fill"]


def category_of(name):
    for cat, names in MEMBERS.items():
        if name in names:
            return cat
    return "system"


def _grad(f):
    for (y0, c0), (y1, c1) in zip(STOPS, STOPS[1:]):
        if y0 <= f <= y1:
            t = (f - y0) / ((y1 - y0) or 1)
            return tuple(round(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
    return STOPS[-1][1]


def chip(cat, font_path):
    from PIL import Image, ImageDraw, ImageFont
    label, col = CATEGORIES[cat]
    S = 4                                   # supersample
    W, H = 300 * S, 64 * S
    font = ImageFont.truetype(font_path, 34 * S)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=NAVY + (CHIP_ALPHA,),
                        outline=col + (255,), width=3 * S)
    bb = d.textbbox((0, 0), label, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    dot = 10 * S
    gap = 14 * S
    x0 = (W - (dot + gap + tw)) // 2
    cy = H // 2
    d.ellipse((x0, cy - dot // 2, x0 + dot, cy + dot // 2), fill=col + (255,))
    d.text((x0 + dot + gap - bb[0], cy - th // 2 - bb[1]), label, font=font, fill=col + (255,))
    return im.resize((W // S, H // S), Image.LANCZOS)


# ---- pictograms: drawn as an L mask on a 100x100 grid, then gradient-filled + navy outline ----
def _poly(d, pts, s, fill=255):
    d.polygon([(x * s, y * s) for x, y in pts], fill=fill)


def _draw_icon(name, d, s):
    """Draw glyph `name` into ImageDraw d (mask), unit = s px per grid cell (100 grid)."""
    R = lambda x0, y0, x1, y1, r=0, f=255: d.rounded_rectangle(  # noqa: E731
        (x0 * s, y0 * s, x1 * s, y1 * s), radius=r * s, fill=f)
    E = lambda cx, cy, r, f=255: d.ellipse(  # noqa: E731
        ((cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s), fill=f)
    if name == "fighters":            # impact burst
        pts = []
        for i in range(24):
            r = 46 if i % 2 == 0 else 26
            a = math.pi * 2 * i / 24 - math.pi / 2
            pts.append((50 + r * math.cos(a), 50 + r * math.sin(a)))
        _poly(d, pts, s)
        E(50, 50, 11, 0)
    elif name == "party":             # five-point star
        pts = []
        for i in range(10):
            r = 46 if i % 2 == 0 else 19
            a = math.pi * i / 5 - math.pi / 2
            pts.append((50 + r * math.cos(a), 52 + r * math.sin(a)))
        _poly(d, pts, s)
    elif name == "beat-em-up":        # fist: palm + four knuckles + thumb
        R(22, 44, 78, 86, 8)
        for i in range(4):
            E(28 + i * 15, 38, 8.5)
        R(10, 50, 30, 66, 7)
        for i in range(1, 4):         # finger gaps
            R(20 + i * 15 - 0.8, 40, 20 + i * 15 + 0.8, 52, 0, 0)
    elif name == "shmups":            # ship + two shots
        _poly(d, [(50, 8), (62, 48), (92, 70), (92, 80), (60, 72), (56, 88), (44, 88), (40, 72),
                  (8, 80), (8, 70), (38, 48)], s)
        R(44, 52, 56, 64, 3, 0)
    elif name == "platformers":       # three stepped ledges + a hopper
        R(6, 76, 40, 90, 3)
        R(34, 58, 68, 72, 3)
        R(62, 40, 96, 54, 3)
        E(80, 22, 10)
    elif name == "puzzle":            # T-block + L-block
        for x, y in [(14, 14), (36, 14), (58, 14), (36, 36)]:
            R(x, y, x + 20, y + 20, 3)
        for x, y in [(58, 58), (58, 80), (36, 80), (14, 80)]:
            R(x, y, x + 20, y + 18, 3)
    elif name == "racing":            # checkered flag on a pole
        R(12, 8, 20, 94, 3)
        for r in range(4):
            for c in range(5):
                if (r + c) % 2 == 0:
                    R(20 + c * 14, 10 + r * 14, 34 + c * 14, 24 + r * 14)
    elif name == "sports":            # ball with seams
        E(50, 50, 44)
        d.arc((-30 * s, 8 * s, 40 * s, 92 * s), -60, 60, fill=0, width=int(4 * s))
        d.arc((60 * s, 8 * s, 130 * s, 92 * s), 120, 240, fill=0, width=int(4 * s))
        R(48, 8, 52, 92, 0, 0)
        R(8, 48, 92, 52, 0, 0)
    elif name == "run-and-gun":       # crosshair
        E(50, 50, 38)
        E(50, 50, 28, 0)
        R(46, 4, 54, 34)
        R(46, 66, 54, 96)
        R(4, 46, 34, 54)
        R(66, 46, 96, 54)
        E(50, 50, 6)
    elif name == "twin-stick":        # two stick tops
        for cx in (27, 73):
            E(cx, 50, 21)
            E(cx, 50, 14, 0)
            E(cx, 50, 8)
    elif name == "quit":              # exit: an open door frame, an arrow leaving it to the right (#218)
        R(8, 8, 50, 20, 4)
        R(8, 80, 50, 92, 4)
        R(8, 8, 20, 92, 4)
        R(38, 8, 50, 30, 4)
        R(38, 70, 50, 92, 4)
        R(34, 43, 72, 57, 3)
        _poly(d, [(64, 26), (96, 50), (64, 74)], s)
    elif name == "maze":              # square spiral
        path = [(8, 8), (92, 8), (92, 92), (8, 92), (8, 26), (74, 26), (74, 74), (26, 74), (26, 44),
                (56, 44), (56, 58)]
        d.line([(x * s, y * s) for x, y in path], fill=255, width=int(11 * s), joint="curve")
        for x, y in path:
            E(x, y, 5.5)
    else:
        raise KeyError(name)


def icon(name):
    from PIL import Image, ImageDraw, ImageFilter
    SIZE, S, PADPX, STROKE = 192, 4, 14, 7
    big = SIZE * S
    inner = (SIZE - 2 * PADPX) * S
    mask = Image.new("L", (inner, inner), 0)
    _draw_icon(name, ImageDraw.Draw(mask), inner / 100.0)
    full = Image.new("L", (big, big), 0)
    full.paste(mask, (PADPX * S, PADPX * S))
    full = full.resize((SIZE, SIZE), Image.LANCZOS)
    outline = full.filter(ImageFilter.MaxFilter(STROKE * 2 + 1)).point(lambda v: 255 if v > 40 else 0)
    col = Image.new("RGBA", (1, SIZE))
    for y in range(SIZE):
        col.putpixel((0, y), _grad(y / (SIZE - 1)) + (255,))
    grad = col.resize((SIZE, SIZE), Image.NEAREST)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (SIZE, SIZE), NAVY + (255,)), (0, 0), outline)
    out.paste(grad, (0, 0), full)
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print(__doc__.split("\n\n")[0] + "\nusage: gen_chips.py [THEME_DIR]", file=sys.stderr)
        return 2
    theme = os.path.abspath(argv[0]) if argv else DEFAULT_THEME
    font = os.path.join(theme, "_inc", "fonts", BRAND["fonts"]["display"]["file"])
    if not os.path.isfile(font) or not os.path.isdir(os.path.join(theme, "_inc", "systems")):
        print("error: not a theme dir (font / _inc/systems missing): %s" % theme, file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow missing; use a venv (see skills/badges/SKILL.md)", file=sys.stderr)
        return 2
    chips_dir = os.path.join(theme, "_inc", "chips")
    icons_dir = os.path.join(theme, "_inc", "icons")
    os.makedirs(chips_dir, exist_ok=True)
    os.makedirs(icons_dir, exist_ok=True)
    try:
        from PIL import Image
        cache = {c: chip(c, font) for c in CATEGORIES}
        names = sorted(f[:-4] for f in os.listdir(os.path.join(theme, "_inc", "systems"))
                       if f.endswith(".png"))
        for n in names:
            cache[category_of(n)].save(os.path.join(chips_dir, n + ".png"))
        cache["system"].save(os.path.join(chips_dir, "default.png"))
        for n in ICONS:
            icon(n).save(os.path.join(icons_dir, n + ".png"))
        Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(os.path.join(icons_dir, "default.png"))
    except Exception as e:  # noqa: BLE001
        print("error: %s" % e, file=sys.stderr)
        return 1
    print("wrote %d chips + %d icons under %s/_inc" % (len(names) + 1, len(ICONS) + 1, theme))
    return 0


if __name__ == "__main__":
    sys.exit(main())
