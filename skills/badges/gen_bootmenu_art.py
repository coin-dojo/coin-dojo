#!/usr/bin/env python3
"""Boot menu card art (#218, #263): two pre-scaled crops of the night backdrop for the boot menu's cards.

    gen_bootmenu_art.py [SRC.png] [OUT_DIR]

SRC defaults to themes/coin-dojo/_inc/art/backdrops/default.png (the night art) and OUT_DIR to
themes/coin-dojo/_inc/art/bootmenu: the theme deploy ships that folder, so the files land in
~/ES-DE/themes/coin-dojo/_inc/art/bootmenu, where bootmenu_app.py looks. Writes esde.png and fightcade.png at exactly
ILL_W x ILL_H (the app then blits them 1:1, no scaling on the Pi). Without the files the app draws its own illustration
(skills/settings/bootmenu_app.py); your own art folder (hk_theme_art_dir) or a personal overlay can replace them.

CROPS are fractions of the source (left, top, right): the height follows the card's aspect. Change them for other art.
Only dependency: Pillow (~/.venvs/badges). Exit codes: 0 ok, 2 bad usage / missing source / no Pillow, 1 render failure.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(REPO, "themes", "coin-dojo", "_inc", "art", "backdrops", "default.png")
OUT = os.path.join(REPO, "themes", "coin-dojo", "_inc", "art", "bootmenu")
ILL_W, ILL_H = 618, 318          # = bootmenu_app.ILL_W, ILL_H (tests/test_gen_bootmenu_art.py pins it)
CROPS = {
    "esde": (0.0, 0.20, 0.58),       # ARCADE: the left of the scene
    "fightcade": (0.20, 0.04, 0.92),  # FIGHTCADE: the wide centre of the scene
}


def crop_box(size, frac):
    """(left, top, right) fractions -> a pixel box with the card's aspect, kept inside the image."""
    w, h = size
    left, top, right = frac
    bw = (right - left) * w
    bh = bw * ILL_H / float(ILL_W)
    if bh > h:
        bh, bw = float(h), h * ILL_W / float(ILL_H)
    x0 = min(max(0.0, left * w), w - bw)
    y0 = min(max(0.0, top * h), h - bh)
    return int(round(x0)), int(round(y0)), int(round(x0 + bw)), int(round(y0 + bh))


def render(src, out_dir):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for key, frac in CROPS.items():
        tile = im.crop(crop_box(im.size, frac)).resize((ILL_W, ILL_H), Image.LANCZOS)
        path = os.path.join(out_dir, key + ".png")
        tile.save(path, optimize=True)
        out.append(path)
    return out


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) > 2 or any(x.startswith("-") for x in a):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    src = a[0] if a else SRC
    out = a[1] if len(a) > 1 else OUT
    if not os.path.isfile(src):
        print("error: no source art: %s" % src, file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow missing; use a venv (see skills/badges/SKILL.md)", file=sys.stderr)
        return 2
    try:
        for p in render(src, out):
            print("wrote %s %dx%d" % (p, ILL_W, ILL_H))
    except Exception as e:  # noqa: BLE001
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
