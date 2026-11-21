#!/usr/bin/env python3
"""Generate the neutral default system backdrop (1440x1080): dark indigo vertical
gradient + faint scanline texture + soft vignette. Original art (CC BY-SA 4.0).
Usage: make_backdrop.py OUT.png   (needs Pillow, see SKILL.md)"""
import sys
from PIL import Image

W, H = 1440, 1080
TOP, BOT = (24, 16, 64), (8, 6, 26)


def build():
    im = Image.new("RGB", (W, H))
    px = im.load()
    cx, cy = W / 2, H / 2
    for y in range(H):
        t = y / (H - 1)
        base = [TOP[i] + (BOT[i] - TOP[i]) * t for i in range(3)]
        dark = 0.88 if y % 3 == 2 else 1.0
        for x in range(W):
            d = (((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2) / 2
            v = (1 - 0.35 * d) * dark
            px[x, y] = tuple(int(c * v) for c in base)
    return im


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    build().save(sys.argv[1])
    print("wrote", sys.argv[1], "%dx%d" % (W, H))
