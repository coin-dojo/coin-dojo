#!/usr/bin/env python3
"""Accent frames for the gamelist panels: lavender border + amber->red top
edge, matching the shutdown-card look. Transparent center so the panel art and
media show through. One PNG per panel at its native pixel size (no stretch)."""
from PIL import Image, ImageDraw
import os, sys

CYAN  = (0xC9, 0xC0, 0xEA, 255)   # lavender border (was cyan; the name is kept)
AMBER = (0xFF, 0xC9, 0x4D, 255)
RED   = (0xE2, 0x3B, 0x3B, 255)
B     = 2    # lavender border thickness (px)
EDGE  = 7    # amber->red top edge thickness (px)

def lerp(a, b, t):
    return tuple(round(a[i] + (b[i]-a[i])*t) for i in range(4))

def frame(w, h, out):
    im = Image.new('RGBA', (w, h), (0,0,0,0))
    d = ImageDraw.Draw(im)
    # cyan border (4 rects)
    d.rectangle([0,0,w-1,B-1], fill=CYAN)            # top
    d.rectangle([0,h-B,w-1,h-1], fill=CYAN)          # bottom
    d.rectangle([0,0,B-1,h-1], fill=CYAN)            # left
    d.rectangle([w-B,0,w-1,h-1], fill=CYAN)          # right
    # amber->red horizontal gradient on the very top edge (over the cyan)
    for x in range(w):
        t = x/(w-1)
        d.line([(x,0),(x,EDGE-1)], fill=lerp(AMBER, RED, t))
    im.save(out)
    print('wrote', out, im.size)

OUT = sys.argv[1] if len(sys.argv) > 1 else 'frames'
os.makedirs(OUT, exist_ok=True)
# panelLeft  0.40 x 0.655  of 1440x1080  -> 576 x 707
frame(576, 707, OUT + '/panel-left.png')
# panelRight 0.52 x 0.655  of 1440x1080  -> 749 x 707
frame(749, 707, OUT + '/panel-right.png')
