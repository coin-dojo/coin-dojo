"""Folder art for the nested menus (Pillow; inputs are only ever opened as images).
- grids: <S>/fshots/<virtual>--<folder>--<i>.img -> 2x2 grid, each shot letterboxed (aspect kept) in a 4:3 cell
- consoles: <S>/sysphotos/<system>.png|jpg -> centred product photo (white JPG background keyed out)
Both in the theme frame (navy, gold border). Output <S>/folder_photos/<virtual>/<folder>.png + preview.png."""
import os
import sys
from collections import defaultdict

from PIL import Image, ImageDraw

S = sys.argv[1]
OUT = os.path.join(S, "folder_photos")
GOLD, NAVY, CELL = (232, 184, 64, 255), (20, 16, 48, 255), (8, 6, 20, 255)
CW, CH, GAP, PAD = 320, 240, 8, 10
W, H = PAD * 2 + CW * 2 + GAP, PAD * 2 + CH * 2 + GAP
CONSOLES = ["nes", "snes", "genesis", "segacd", "saturn", "dreamcast", "n64", "psx", "pcengine", "neogeo"]


def frame():
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=14, fill=NAVY, outline=GOLD, width=4)
    return img, d


def fit(im, w, h):
    im = im.copy()
    im.thumbnail((w, h), Image.LANCZOS)
    return im


def grid(paths):
    img, d = frame()
    for i, p in enumerate(paths[:4]):
        x, y = PAD + (i % 2) * (CW + GAP), PAD + (i // 2) * (CH + GAP)
        if len(paths) == 3 and i == 2:            # three shots (a 3-game series): the last one sits centred below
            x = (W - CW) // 2
        d.rectangle([x, y, x + CW - 1, y + CH - 1], fill=CELL)
        shot = Image.open(p).convert("RGB")
        r = shot.width / shot.height
        if 1.2 <= r <= 1.45:                      # ~4:3 already: fill the cell
            shot = shot.resize((CW, CH), Image.LANCZOS)
        else:                                     # vertical (TATE) or wide: letterbox, keep aspect
            shot = fit(shot, CW, CH)
        img.paste(shot, (x + (CW - shot.width) // 2, y + (CH - shot.height) // 2))
        d.rectangle([x - 1, y - 1, x + CW, y + CH], outline=GOLD, width=2)
    return img


def key_white(im):
    """JPG product shots on white: make the near-white border region transparent (flood from the edges)."""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    seen, stack = set(), [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] + \
        [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or not (0 <= x < w and 0 <= y < h):
            continue
        seen.add((x, y))
        r, g, b, a = px[x, y]
        if r > 235 and g > 235 and b > 235:
            px[x, y] = (r, g, b, 0)
            stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return im


def console(path):
    img, d = frame()
    # soft light stage so black consoles read on the navy panel
    d.rounded_rectangle([PAD, PAD, W - PAD - 1, H - PAD - 1], radius=10, fill=(214, 206, 232, 255))
    ph = Image.open(path)
    ph = ph.convert("RGBA")
    if ph.getchannel("A").getextrema()[0] == 255:   # no transparency (JPG, or PNG on white): key out the white
        ph = key_white(ph)
    ph = fit(ph, W - PAD * 2 - 40, H - PAD * 2 - 40)
    img.alpha_composite(ph, ((W - ph.width) // 2, (H - ph.height) // 2))
    return img


made = []
groups = defaultdict(dict)
for f in os.listdir(os.path.join(S, "fshots")):
    v, folder, i = f[:-4].split("--")
    groups[(v, folder)][int(i)] = os.path.join(S, "fshots", f)
for (v, folder), shots in sorted(groups.items()):
    os.makedirs(os.path.join(OUT, v), exist_ok=True)
    out = os.path.join(OUT, v, folder + ".png")
    grid([shots[i] for i in sorted(shots)]).save(out)
    made.append(out)
for sysname in CONSOLES:
    src = next((os.path.join(S, "sysphotos", sysname + e) for e in (".png", ".jpg")
                if os.path.exists(os.path.join(S, "sysphotos", sysname + e))), None)
    if src:
        os.makedirs(os.path.join(OUT, "systems"), exist_ok=True)
        out = os.path.join(OUT, "systems", sysname + ".png")
        console(src).save(out)
        made.append(out)
print(len(made), "images")
# preview: 5 columns, scaled 0.3
sc = 0.3
tw, th = int(W * sc), int(H * sc)
cols = 5
rows = (len(made) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (tw + 8) + 8, rows * (th + 8) + 8), (40, 30, 90))
for k, p in enumerate(sorted(made)):
    t = Image.open(p).resize((tw, th), Image.LANCZOS)
    sheet.paste(t, (8 + (k % cols) * (tw + 8), 8 + (k // cols) * (th + 8)), t)
sheet.save(os.path.join(S, "preview-all.png"))
print("preview", sheet.size, "order:", ", ".join(os.path.relpath(p, OUT)[:-4] for p in sorted(made)))
