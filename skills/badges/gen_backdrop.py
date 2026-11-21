#!/usr/bin/env python3
"""The original Coin Dojo backdrop (#235, cel-shaded in #259): the generated, fully original alternative to the night art
that is the community default since #263 (backdrops/default.png). It stays generator-only in spirit: the committed
themes/coin-dojo/_inc/art/backdrops/dojo.png is its output (copy it over default.png, or into your own
hk_theme_art_dir/backdrops/default.png, to show it behind the carousel and the game lists).

Original art, brand palette only (brand/tokens.json): a martial-arts dojo at night, drawn in an anime-background /
cel style: flat colour fills (a base and at most one hard-edged shadow tone per object), bold dark-violet ink
outlines (thicker on silhouettes than on inner detail), simple chunky shapes, flat floor bands, a flat moon with a
halo ring, and a soft glow only on the shoji paper. The room: a moon window on the left (cool), shoji screens in
the middle with a kamidana shelf and a hanging scroll reading 道場 (dojo; U+9053 U+5834, jisho.org/search/道場,
set in the vendored OFL font Yuji Syuku, subset to those two glyphs: fonts/), a red paper lantern on the right (the
warm accent), clear tatami floor bands, and nothing else: the lower walls and the floor
stay empty, a plain dojo. The theme's CRT scanline treatment is baked in (same 3-row phase as _inc/art/scanlines.png). No characters, no logos, no game art.

The committed themes/coin-dojo/_inc/art/backdrops/dojo.png is this script's output (the esde_theme role deploys
only default.png, the night art; tests/test_ship_no_artifacts.py lists dojo.png by sha256). Deterministic: same
tokens, same font file, same Pillow, same bytes. Needs Pillow (venv, see SKILL.md). Drawn at 2x and
downsampled so the outlines are clean.

The carousel band (y 0.40..0.66: the selected badge, chip and count) and the label rows beside it stay calm: the
moon, the lantern, shelf and scroll sit above it, the props stand below it.

Usage: gen_backdrop.py OUT.png
Exit: 0 ok, 2 bad usage or Pillow missing.
"""
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "brand"))
W, H = 1440, 1080
SS = 2                        # supersampling: draw at 2x, shrink with LANCZOS
SEED = 259
BAND = (0.40, 0.66)           # the carousel band (fractions of H): nothing bright in here
BEAM = 0.105                  # bottom of the lintel beam over the screens
HORIZON = 0.70                # the wall meets the floor here
POSTS = (0.0, 0.265, 0.735, 1.0)   # wooden posts (centre x), the screens sit between them
POST_W = 0.034
MOON_WIN = (0.135, 0.265, 0.082)   # round window: centre x, centre y (of H), radius (of W)
LANTERN = (0.865, 0.255)      # paper lantern centre
SCANLINE = 0.90               # every third row (y % 3 == 0, the overlay's phase) is darkened to this
SCROLL_TEXT = "\u9053\u5834"   # 道場 (dojo), written top to bottom; test_gen_backdrop asserts these code points
FONT = os.path.join(HERE, "fonts", "YujiSyuku-dojo-subset.ttf")   # OFL 1.1, fonts/OFL-YujiSyuku.txt
OUTLINE = (5, 3)              # ink outline widths at 1080p: silhouettes, inner detail
FLOOR_EDGES = (0.775, 0.86, 0.94)   # the flat tatami bands start here (fractions of H, after the wall line HORIZON)
# Where the carousel labels sit in the 1440x1080 render (FIGHTERS left, SHMUPS right; x0, y0, x1, y1), measured from
# `make preview`; the zones under them (EMPTY_ZONES) stay empty (test_gen_backdrop checks).
LABEL_LEFT, LABEL_RIGHT = (65, 550, 330, 600), (1120, 550, 1365, 600)
EMPTY_ZONES = ((70, 640, 310, 790), (1124, 640, 1378, 790))   # lower-left / lower-right wall under the labels: no props
COLS = 3                      # shoji columns per screen
DRAWN = []                    # the strings the last build() set as calligraphy (read by the tests)


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def scale(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def glow(Image, ImageDraw, ImageFilter, size, centre, radius, colour, alpha, blur, squash=1.0):
    """A soft glow (ellipse, height = radius * squash) on a black RGB layer, for screen/add blending."""
    layer = Image.new("RGB", size)
    d = ImageDraw.Draw(layer)
    cx, cy = centre
    ry = radius * squash
    d.ellipse([cx - radius, cy - ry, cx + radius, cy + ry], fill=scale(colour, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


class Cel:
    """A 1x-coordinate drawing surface backed by a 2x image; every shape gets an ink outline."""

    def __init__(self, Image, ImageDraw, ink, sil, inner):
        self.Image, self.ImageDraw = Image, ImageDraw
        self.im = Image.new("RGB", (W * SS, H * SS), ink)
        self.d = ImageDraw.Draw(self.im)
        self.ink, self.sil, self.inner = ink, sil, inner

    def _p(self, pts):
        return [(x * SS, y * SS) for x, y in pts]

    def _w(self, w):
        return max(1, int(round(w * SS)))

    def rect(self, x0, y0, x1, y1, fill, ow=None):
        ow = self.inner if ow is None else ow
        self.d.rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], fill=fill, outline=self.ink if ow else None,
                         width=self._w(ow) if ow else 1)

    def rrect(self, x0, y0, x1, y1, r, fill, ow=None):
        ow = self.sil if ow is None else ow
        self.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=r * SS, fill=fill,
                                 outline=self.ink, width=self._w(ow))

    def ell(self, x0, y0, x1, y1, fill, ow=None):
        ow = self.inner if ow is None else ow
        self.d.ellipse([x0 * SS, y0 * SS, x1 * SS, y1 * SS], fill=fill, outline=self.ink if ow else None,
                       width=self._w(ow) if ow else 1)

    def poly(self, pts, fill, ow=None):
        ow = self.inner if ow is None else ow
        self.d.polygon(self._p(pts), fill=fill)
        if ow:
            self.d.line(self._p(list(pts) + [pts[0]]), fill=self.ink, width=self._w(ow), joint="curve")

    def line(self, pts, fill, w):
        self.d.line(self._p(pts), fill=fill, width=self._w(w))

    def stick(self, p0, p1, w, fill, ow=None):
        """A thick bar with an ink outline (staff, bokken, legs)."""
        ow = self.inner if ow is None else ow
        self.d.line(self._p([p0, p1]), fill=self.ink, width=self._w(w + 2 * ow))
        self.d.line(self._p([p0, p1]), fill=fill, width=self._w(w))
        for q in (p0, p1):
            r = (w / 2.0)
            self.d.ellipse([(q[0] - r) * SS, (q[1] - r) * SS, (q[0] + r) * SS, (q[1] + r) * SS], fill=fill)

    def post(self, x, y0, y1, hw, base, shade, ow=None):
        """A timber post: base tone, one hard-edged shadow strip on the right."""
        self.rect(x - hw, y0, x + hw, y1, base, ow)
        self.d.rectangle([(x + hw * 0.35) * SS, (y0 + 2) * SS, (x + hw - 1) * SS, (y1 - 2) * SS], fill=shade)
        self.rect(x - hw, y0, x + hw, y1, None, ow)

    def done(self):
        return self.im.resize((W, H), self.Image.LANCZOS)


def build(text=None):
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    import brand
    b = brand.load()
    c = {k: b.rgb(k) for k in ("bg", "bg_deep", "panel", "card", "button", "dim", "lavender", "muted", "text",
                                "text_soft", "gold", "gold_muted", "salmon", "crimson", "red_light", "navy",
                                "counter_bg")}
    text = SCROLL_TEXT if text is None else text
    del DRAWN[:]
    rng = random.Random(SEED)
    ink = c["bg"]                                              # very dark violet outlines
    sil, inner = OUTLINE
    cv = Cel(Image, ImageDraw, ink, sil, inner)
    hy, by = int(HORIZON * H), int(BEAM * H)
    wood = mix(c["counter_bg"], c["panel"], 0.30)              # timber: base
    wood_sh = mix(wood, c["bg_deep"], 0.45)                    # timber: hard shadow
    paper = mix(c["panel"], c["dim"], 0.50)                    # shoji paper
    sky = mix(c["navy"], c["panel"], 0.55)                     # left bay: night plaster
    lat = mix(paper, ink, 0.38)                                # inner lattice: ink, softened

    # 1. ceiling, rafters, the wall bays
    cv.rect(0, 0, W, by, mix(c["bg_deep"], c["navy"], 0.5), 0)
    for k in range(4):
        x = W * (k + 0.5) / 4
        cv.rect(x - 12, 0, x + 12, by - 30, wood_sh, inner)
    lx0, lx1 = POSTS[0] * W, POSTS[1] * W
    cv.rect(lx0, by, lx1, hy, sky, 0)
    for i in (1, 2):
        cv.rect(POSTS[i] * W, by, POSTS[i + 1] * W, hy, paper, 0)
    # shoji: a few big panes (fewer in A), a mid rail, lower kick panels
    for i in (1, 2):
        x0, x1 = POSTS[i] * W, POSTS[i + 1] * W
        cols = COLS + (0 if i == 1 else 1)
        for k in range(1, cols):
            x = x0 + (x1 - x0) * k / cols
            cv.line([(x, by), (x, hy)], lat, inner)
        for ry in (0.255, 0.50):
            cv.line([(x0, ry * H), (x1, ry * H)], lat, inner)
    # soft glow on the paper only: moon (cool) on the left bay, lantern (warm) on the right bay
    mx, my, mr = MOON_WIN[0] * W, MOON_WIN[1] * H, MOON_WIN[2] * W
    lx, ly = LANTERN[0] * W, LANTERN[1] * H
    gl = Image.new("RGB", (W, H))
    gl = ImageChops.screen(gl, glow(Image, ImageDraw, ImageFilter, (W, H), (mx, my), mr * 1.8, c["lavender"], 0.16, 50))
    gl = ImageChops.screen(gl, glow(Image, ImageDraw, ImageFilter, (W, H), (lx, ly), 190, c["gold"], 0.40, 55))
    gl = ImageChops.screen(gl, glow(Image, ImageDraw, ImageFilter, (W, H), (lx, ly), 260, c["crimson"], 0.22, 70))
    gmask = Image.new("L", (W, H))
    gd = ImageDraw.Draw(gmask)
    gd.rectangle([lx0 + 10, by, lx1 - 10, hy], fill=255)
    gd.rectangle([POSTS[1] * W + 28, by, POSTS[3] * W - 28, hy], fill=255)
    big = cv.im
    glow_big = ImageChops.screen(big, gl.resize(big.size, Image.BICUBIC))
    cv.im = Image.composite(glow_big, big, gmask.resize(big.size))
    cv.d = ImageDraw.Draw(cv.im)

    # 2. wainscot rail, kickboard, the lintel beam
    cv.rect(0, 0.645 * H, W, hy, wood_sh, inner)
    cv.rect(lx0, 0.60 * H, lx1, 0.61 * H, wood_sh, inner)
    cv.rect(0, by - 28, W, by, wood, sil)
    cv.rect(0, by - 8, W, by, wood_sh, 0)
    cv.rect(0, by - 28, W, by, None, sil)
    for pxr in POSTS:                                          # posts (the screens' frame)
        cv.post(pxr * W, 0, hy, POST_W * W / 2, wood, wood_sh, sil)

    # 3. the round window: flat sky, halo ring, flat moon with one shadow tone, three bamboo bars
    cv.ell(mx - mr - 12, my - mr - 12, mx + mr + 12, my + mr + 12, wood, sil)
    win = Image.new("RGB", cv.im.size, mix(c["navy"], c["dim"], 0.22))
    wd = ImageDraw.Draw(win)
    mcx, mcy, mrr = mx + mr * 0.20, my - mr * 0.12, mr * 0.40
    halo = mix(win.getpixel((5, 5)), c["lavender"], 0.22)
    wd.ellipse([(mcx - mrr * 1.9) * SS, (mcy - mrr * 1.9) * SS, (mcx + mrr * 1.9) * SS, (mcy + mrr * 1.9) * SS], fill=halo)
    wd.ellipse([(mcx - mrr * 1.45) * SS, (mcy - mrr * 1.45) * SS, (mcx + mrr * 1.45) * SS, (mcy + mrr * 1.45) * SS],
               fill=mix(halo, c["lavender"], 0.22))
    wd.ellipse([(mcx - mrr) * SS, (mcy - mrr) * SS, (mcx + mrr) * SS, (mcy + mrr) * SS],
               fill=mix(c["text"], c["text_soft"], 0.35), outline=ink, width=SS * inner)
    wd.chord([(mcx - mrr + 4) * SS, (mcy - mrr + 4) * SS, (mcx + mrr - 4) * SS, (mcy + mrr - 4) * SS], 300, 100,
             fill=mix(c["text_soft"], c["muted"], 0.55))
    wd.ellipse([(mcx - mrr) * SS, (mcy - mrr) * SS, (mcx + mrr) * SS, (mcy + mrr) * SS], outline=ink, width=SS * inner)
    for _ in range(3):                  # flat four-point stars
        sx, sy = rng.uniform(mx - mr * 0.8, mx + mr * 0.8), rng.uniform(my - mr * 0.8, my - mr * 0.3)
        if math.hypot(sx - mcx, sy - mcy) < mrr * 2.1:
            continue
        r = 7
        wd.polygon([(sx * SS, (sy - r) * SS), ((sx + 2) * SS, (sy - 2) * SS), ((sx + r) * SS, sy * SS),
                    ((sx + 2) * SS, (sy + 2) * SS), (sx * SS, (sy + r) * SS), ((sx - 2) * SS, (sy + 2) * SS),
                    ((sx - r) * SS, sy * SS), ((sx - 2) * SS, (sy - 2) * SS)], fill=c["text_soft"])
    for ox in (-0.42, 0.0, 0.42):                              # bamboo bars
        x = mx + ox * mr
        wd.rectangle([(x - 7) * SS, (my - mr) * SS, (x + 7) * SS, (my + mr) * SS], fill=wood, outline=ink, width=SS * inner)
        wd.rectangle([(x + 2) * SS, (my - mr) * SS, (x + 6) * SS, (my + mr) * SS], fill=wood_sh)
        for ny in (-0.5, 0.15, 0.7):
            wd.line([(x - 8) * SS, (my + ny * mr) * SS, (x + 8) * SS, (my + ny * mr) * SS], fill=ink, width=SS * inner)
    wm = Image.new("L", cv.im.size)
    ImageDraw.Draw(wm).ellipse([(mx - mr) * SS, (my - mr) * SS, (mx + mr) * SS, (my + mr) * SS], fill=255)
    cv.im = Image.composite(win, cv.im, wm)
    cv.d = ImageDraw.Draw(cv.im)
    cv.ell(mx - mr, my - mr, mx + mr, my + mr, None, sil)

    # 4. the floor: flat tatami bands, a hard moon patch and a hard lantern patch
    f1, f2 = mix(c["panel"], c["gold_muted"], 0.20), mix(c["panel"], c["gold_muted"], 0.12)
    edges = [hy] + [int(f * H) for f in FLOOR_EDGES] + [H]
    for j in range(len(edges) - 1):
        cv.rect(-sil, edges[j], W + sil, edges[j + 1], f1 if j % 2 == 0 else f2, inner if j else sil)
    patch = mix(f1, c["lavender"], 0.14)
    cv.poly([(mx - 70, hy), (mx + 50, hy), (mx + 160, H - 2), (mx - 110, H - 2)], patch, 0)
    warm = mix(f1, c["gold"], 0.13)
    cv.poly([(lx - 80, hy), (lx + 60, hy), (lx + 120, edges[-2]), (lx - 150, edges[-2])], warm, 0)
    for j in range(1, len(edges) - 1):                         # re-ink the band seams over the patches
        cv.line([(0, edges[j]), (W, edges[j])], ink, inner)
    cv.line([(0, hy), (W, hy)], ink, sil)

    # (the lower walls and the floor stay empty: a plain dojo, #259)

    # 6. the kamidana shelf (high on the middle screens, above the band)
    kx, ky = 0.405 * W, 0.225 * H
    cv.rect(kx - 100, ky, kx + 100, ky + 12, wood, sil)
    cv.rect(kx - 98, ky + 7, kx + 98, ky + 10, wood_sh, 0)
    for bx in (-72, 72):
        cv.poly([(kx + bx - 8, ky + 12), (kx + bx + 8, ky + 12), (kx + bx, ky + 46)], wood_sh, inner)
    cv.rect(kx - 36, ky - 46, kx + 36, ky, wood, sil)
    cv.rect(kx + 8, ky - 44, kx + 34, ky - 2, wood_sh, 0)
    cv.rect(kx - 16, ky - 36, kx + 16, ky, c["bg"], inner)
    cv.line([(kx, ky - 36), (kx, ky)], c["gold"], inner)
    cv.poly([(kx - 54, ky - 42), (kx + 54, ky - 42), (kx + 34, ky - 72), (kx - 34, ky - 72)], mix(wood, ink, 0.35), sil)
    cv.poly([(kx + 6, ky - 42), (kx + 54, ky - 42), (kx + 34, ky - 72), (kx + 14, ky - 72)], mix(wood, ink, 0.55), 0)
    cv.poly([(kx - 54, ky - 42), (kx + 54, ky - 42), (kx + 34, ky - 72), (kx - 34, ky - 72)], None, sil)
    cv.rect(kx - 5, ky - 82, kx + 5, ky - 72, c["gold"], inner)
    for sx_ in (-72, 72):                                      # sakaki sprigs
        cv.rect(kx + sx_ - 8, ky - 16, kx + sx_ + 8, ky, wood_sh, inner)
        for a in (-0.55, 0.0, 0.55):
            cv.stick((kx + sx_, ky - 16), (kx + sx_ + a * 50, ky - 56), 8, mix(c["gold_muted"], c["bg"], 0.62), 2)

    # 7. the hanging scroll: crimson mount, paper, 道場 written top to bottom in brush ink, a red seal
    sx0, sy0, sw, sh = 0.590 * W, 0.140 * H, 124, 262
    cv.line([(sx0 + sw / 2, by), (sx0 + sw / 2, sy0)], ink, inner + 1)
    cv.rect(sx0 - 10, sy0 - 8, sx0 + sw + 10, sy0 + 4, wood, sil)
    mount = mix(c["crimson"], c["bg"], 0.62)
    cv.rect(sx0, sy0 + 4, sx0 + sw, sy0 + sh, mount, sil)
    cv.rect(sx0 + sw - 14, sy0 + 6, sx0 + sw - 2, sy0 + sh - 2, mix(mount, ink, 0.45), 0)
    cv.rect(sx0 + 11, sy0 + 18, sx0 + sw - 11, sy0 + sh - 14, mix(c["text_soft"], c["dim"], 0.40), inner)
    cv.rect(sx0 + sw - 25, sy0 + 20, sx0 + sw - 13, sy0 + sh - 16, mix(c["text_soft"], c["dim"], 0.60), 0)
    cv.rect(sx0 - 12, sy0 + sh, sx0 + sw + 12, sy0 + sh + 12, wood, sil)
    for ex in (-14, sw + 14):
        cv.ell(sx0 + ex - 6, sy0 + sh - 1, sx0 + ex + 6, sy0 + sh + 13, c["gold"], inner)
    font = ImageFont.truetype(FONT, 72 * SS)
    cx = sx0 + sw / 2 - 6
    DRAWN.append(text)
    for i, ch in enumerate(text):
        gy = sy0 + 24 + i * 100
        cv.d.text((cx * SS, (gy + 38) * SS), ch, font=font, fill=ink, anchor="mm")
    cv.rect(sx0 + 17, sy0 + sh - 50, sx0 + 33, sy0 + sh - 34, c["crimson"], inner)     # seal, bottom left

    # 8. the red paper lantern (chunky, bold ribs, gold caps) and its cord
    lw, lh = 0.052 * W, 0.098 * H
    cv.line([(lx, by), (lx, ly - lh - 8)], ink, inner + 1)
    cv.rect(lx - lw * 0.62, ly - lh - 14, lx + lw * 0.62, ly - lh + 8, c["gold"], sil)
    cv.rect(lx - lw * 0.62, ly + lh - 8, lx + lw * 0.62, ly + lh + 14, c["gold"], sil)
    cv.ell(lx - lw, ly - lh, lx + lw, ly + lh, c["crimson"], sil)
    cv.d.chord([(lx - lw + 5) * SS, (ly - lh + 5) * SS, (lx + lw - 5) * SS, (ly + lh - 5) * SS], 285, 105,
               fill=mix(c["crimson"], c["bg"], 0.38))
    for k in range(1, 4):
        t = -1 + 2 * k / 4
        half = lw * math.sqrt(max(0.0, 1 - t * t))
        cv.line([(lx - half, ly + t * lh), (lx + half, ly + t * lh)], ink, inner)
    cv.ell(lx - lw, ly - lh, lx + lw, ly + lh, None, sil)
    cv.line([(lx, ly + lh + 14), (lx, ly + lh + 44)], c["crimson"], 4)

    # 9. flat motes (embers and moon dust), none inside the carousel band
    for _ in range(14):
        fx, fy, sp = rng.choice(((MOON_WIN[0] + 0.07, MOON_WIN[1] + 0.12, 0.09), LANTERN + (0.07,)))
        x, y = rng.gauss(fx * W, W * sp), rng.gauss(fy * H, H * sp)
        if not (0 <= x < W and 0 <= y < hy) or BAND[0] * H < y < BAND[1] * H:
            continue
        r = rng.choice((2, 3, 3, 4))
        cv.ell(x - r, y - r, x + r, y + r, rng.choice((c["gold"], c["text_soft"], c["lavender"])), 0)

    im = cv.done()
    # 10. CRT scanlines, in phase with the theme's scanlines.png overlay (its dark row is y % 3 == 0)
    lines = Image.new("L", (W, H), 255)
    ld = ImageDraw.Draw(lines)
    for y in range(0, H, 3):
        ld.line([0, y, W, y], fill=int(255 * SCANLINE))
    return ImageChops.multiply(im, Image.merge("RGB", (lines, lines, lines)))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        im = build()
    except ImportError:
        print("error: Pillow missing; use a venv (see SKILL.md)", file=sys.stderr)
        return 2
    im.save(argv[0], optimize=True)    # < 400 kB (themes/tests/test_theme_config.py)
    print("wrote %s (%dx%d)" % (argv[0], W, H))
    return 0


if __name__ == "__main__":
    sys.exit(main())
