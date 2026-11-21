#!/usr/bin/env python3
"""Tests for gen_backdrop.py, the original Coin Dojo backdrop (#235; cel-shaded with the 道場 scroll, #259; the alternative to the night art, #263). Needs Pillow; SKIP if absent.
Run: python3 skills/badges/tests/test_gen_backdrop.py   (or ~/.venvs/badges/bin/python ...)
Broken cases: no output path, a flag, two arguments -> exit 2 and nothing written; a stale or missing committed
dojo.png is reported; a flat card, a bright carousel band, a backdrop without the scanline phase and the mirrored
dojo (lantern on the cool side) each fail art_problems()."""
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(os.path.dirname(HERE), "gen_backdrop.py")
COMMITTED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "themes", "coin-dojo", "_inc", "art", "backdrops", "dojo.png"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "lib"))
from hktest import check, finish, main, skip  # noqa: E402  (shared helper; SKIPs counted in RESULT, #152)

try:
    from PIL import Image, ImageOps, ImageStat
except ImportError:  # counted SKIP (#135): the Forgejo runner image has no Pillow
    skip("Pillow not installed")
    sys.exit(finish())


def load_gen():
    spec = importlib.util.spec_from_file_location("gen_backdrop_under_test", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scroll_problems(drawn, font_path):
    """-> what is wrong with the calligraphy `drawn` (the strings set on the scroll) or its font (empty = ok):
    exactly 道場 (U+9053 then U+5834, vertical order top to bottom) and a font that really has both glyphs."""
    from PIL import ImageFont
    out = []
    if drawn != ["\u9053\u5834"]:
        out.append("scroll text is %s, want exactly U+9053 U+5834 (道場)" % [[hex(ord(ch)) for ch in t] for t in drawn])
    if not os.path.isfile(font_path):
        return out + ["vendored font missing: %s" % font_path]
    from PIL import ImageDraw
    font = ImageFont.truetype(font_path, 80)

    def glyph(ch):
        g = Image.new("L", (100, 100))
        ImageDraw.Draw(g).text((10, 10), ch, font=font, fill=255)
        return g.tobytes()
    for ch in "\u9053\u5834":                        # the subset has no 'A': that renders as .notdef, the reference
        if glyph(ch) == glyph("A") or not any(glyph(ch)):
            out.append("font has no glyph for U+%04X" % ord(ch))
    return out


def zone_problems(im, zones):
    """-> what stands on the lower walls under the FIGHTERS / SHMUPS label rows (empty = plain wall): the zones must
    carry no warm accent pixels (gold, crimson) and only the wall's own few vertical edges (a prop adds hundreds)."""
    out = []
    for z in zones:
        crop = im.crop(z)
        raw = crop.convert("RGB").tobytes()
        warm = sum(1 for r, b in zip(raw[0::3], raw[2::3]) if r > 150 and r > b + 60)
        luma = crop.convert("L")
        w, h = luma.size
        px = luma.load()
        edges = sum(1 for y in range(h) for x in range(1, w) if abs(px[x, y] - px[x - 1, y]) > 14)
        if warm > 30:
            out.append("zone %s has %d warm accent pixels: a prop on the wall" % (z, warm))
        if edges > 150:
            out.append("zone %s has %d vertical edges: a prop on the wall" % (z, edges))
    return out


def floor_problems(im, gen):
    """-> a problem when something stands on the floor: dark ink pixels below the wall decoration, away from the
    band seams (the floor is flat bands and light patches only)."""
    h = im.size[1]
    seams = [int(f * h) for f in gen.FLOOR_EDGES]
    ink = 0
    for y in range(int(gen.EMPTY_ZONES[0][3]) + 10, h):
        if any(abs(y - sy) <= 8 for sy in seams) or y % 3 == 0 or y > h - 12:
            continue
        row = im.crop((0, y, im.size[0], y + 1)).convert("L").histogram()
        ink += sum(row[:28])
    return "floor not clear: %d ink pixels below the wall decoration" % ink if ink > 200 else ""


def run(*args):
    return subprocess.run([sys.executable, GEN, *args], capture_output=True, text=True)


def stale(generated, committed):
    """-> a problem string when the committed backdrop is not the generator's output, else ''."""
    if not os.path.isfile(committed):
        return "committed backdrop missing: %s" % committed
    if open(generated, "rb").read() != open(committed, "rb").read():
        return "committed dojo.png is stale: rerun gen_backdrop.py %s" % committed
    return ""


def art_problems(im):
    """-> what makes `im` unfit as the Coin Dojo backdrop behind the carousel (empty = ok)."""
    out = []
    if im.size != (1440, 1080) or im.mode != "RGB":
        return ["want a 1440x1080 RGB image, got %s %s" % (im.size, im.mode)]
    small = im.convert("L").resize((144, 108))
    lo, hi = small.getextrema()
    if hi - lo <= 60:
        out.append("flat: luma range %d (want > 60)" % (hi - lo))
    band, top = small.crop((0, 43, 144, 71)), small.crop((0, 10, 144, 38))   # carousel band y .40-.66, art above
    bs, ts = ImageStat.Stat(band), ImageStat.Stat(top)
    if bs.mean[0] > ts.mean[0]:
        out.append("carousel band brighter than the art above it (%.0f vs %.0f)" % (bs.mean[0], ts.mean[0]))
    if bs.stddev[0] > ts.stddev[0] / 2:
        out.append("carousel band busy: luma stddev %.1f, want <= half the top's %.1f" % (bs.stddev[0], ts.stddev[0]))
    # warm accent right (the lantern), cool left (the moon window)
    warm = ImageStat.Stat(im.crop((1150, 180, 1340, 370))).mean
    cool = ImageStat.Stat(im.crop((100, 160, 330, 410))).mean
    if not warm[0] > warm[2] + 30:
        out.append("no warm lantern on the right (R %.0f vs B %.0f)" % (warm[0], warm[2]))
    if not cool[2] > cool[0]:
        out.append("no cool moonlight on the left (B %.0f vs R %.0f)" % (cool[2], cool[0]))
    # CRT scanlines in the theme overlay's phase: rows y % 3 == 0 darker than the rows after them
    luma = im.convert("L")
    w = luma.size[0]
    rows = [ImageStat.Stat(luma.crop((0, y, w, y + 1))).mean[0] for y in range(120, 420)]
    dark = sum(rows[i] for i in range(0, len(rows), 3)) / (len(rows) / 3)
    lit = sum(rows[i] for i in range(1, len(rows), 3)) / (len(rows) / 3)
    if not dark < lit * 0.95:
        out.append("no scanline treatment in phase y%%3==0 (%.1f vs %.1f)" % (dark, lit))
    return out


def test_backdrop():
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.png"), os.path.join(d, "b.png")
        check(run(a).returncode == 0 and run(b).returncode == 0, "generates")
        check(open(a, "rb").read() == open(b, "rb").read(), "deterministic: same bytes twice")
        bad = stale(a, COMMITTED)
        check(not bad, "the committed dojo.png is this script's output %s" % bad)
        im = Image.open(a)
        check(im.size == (1440, 1080) and im.format == "PNG" and im.mode == "RGB", "1440x1080 RGB PNG: %s %s %s" % (im.size, im.format, im.mode))
        im = im.convert("RGB")
        probs = art_problems(im)
        check(not probs, "the dojo backdrop reads behind the carousel: calm band, warm lantern, cool moon, scanlines (%s)" % "; ".join(probs))
        # broken: each failure the check exists for is reported
        check(any("flat" in p for p in art_problems(Image.new("RGB", (1440, 1080), (30, 20, 60)))),
              "BROKEN: a flat card is reported")
        lit = im.copy()
        lit.paste((255, 240, 200), (0, 432, 1440, 713))
        check(any("band" in p for p in art_problems(lit)), "BROKEN: a bright carousel band is reported")
        busy = im.copy()
        for x in range(0, 1440, 40):
            busy.paste((255, 201, 77), (x, 432, x + 20, 713))
        check(any("busy" in p for p in art_problems(busy)), "BROKEN: a busy carousel band is reported")
        check(any("lantern" in p for p in art_problems(ImageOps.mirror(im))),
              "BROKEN: the mirrored dojo (lantern on the cool side) is reported")
        check(any("scanline" in p for p in art_problems(im.resize((1440, 360)).resize((1440, 1080), Image.NEAREST))),
              "BROKEN: a backdrop without the scanline phase is reported")
        other = os.path.join(d, "other.png")
        Image.new("RGB", (1440, 1080)).save(other)
        check(stale(a, other) != "", "BROKEN: a stale committed dojo.png is reported")
        check(stale(a, os.path.join(d, "none.png")) != "", "BROKEN: a missing committed dojo.png is reported")
        for args, why in (((), "no output path"), (("--x",), "a flag"), (("--style", "B", "a"), "the removed --style"),
                          (("a", "b"), "two arguments")):
            check(run(*args).returncode == 2, "BROKEN: %s -> exit 2" % why)
        check(sorted(os.listdir(d)) == ["a.png", "b.png", "other.png"], "nothing stray written")



def test_calligraphy():
    gen = load_gen()
    im = gen.build()
    check(gen.DRAWN == ["\u9053\u5834"], "the scroll is drawn with exactly 道場 (U+9053, U+5834): %r" % gen.DRAWN)
    check([hex(ord(ch)) for ch in gen.DRAWN[0]] == ["0x9053", "0x5834"], "vertical order: 道 (U+9053) above 場 (U+5834)")
    check(not scroll_problems(gen.DRAWN, gen.FONT), "scroll text and vendored font ok: %s" % scroll_problems(gen.DRAWN, gen.FONT))
    # the ink really lands on the scroll: dark brush pixels in the top glyph's box and in the bottom glyph's box
    box = lambda i: im.crop((int(0.590 * 1440) + 25, int(0.140 * 1080) + 24 + i * 100, int(0.590 * 1440) + 99, int(0.140 * 1080) + 100 + i * 100))
    for i in (0, 1):
        dark = sum(box(i).convert("L").histogram()[:60])
        check(dark > 300, "glyph %d has brush ink on the scroll (%d dark px)" % (i, dark))
    # broken: a changed, a swapped, a missing and an extra character, and a font without the glyphs
    check(scroll_problems(["\u9053\u5834"], gen.FONT) == [], "control: the right text is accepted")
    for bad, why in ((["\u9053\u5835"], "a changed character"), (["\u5834\u9053"], "swapped order"), ([], "nothing drawn"),
                     (["\u9053"], "a missing character"), (["\u9053\u5834\u9053"], "an extra character")):
        check(scroll_problems(bad, gen.FONT) != [], "BROKEN: %s is reported" % why)
    other = gen.build(text="\u9053\u5835")
    check(gen.DRAWN == ["\u9053\u5835"] and other.tobytes() != im.tobytes(), "BROKEN: another text renders a different image")


def test_plain_walls_and_clear_floor():
    gen = load_gen()
    im = gen.build()
    probs = zone_problems(im, gen.EMPTY_ZONES)
    check(not probs, "the lower walls under FIGHTERS and SHMUPS are plain (%s)" % "; ".join(probs))
    check(floor_problems(im, gen) == "", "the floor is clear (flat bands and light patches only): %s" % floor_problems(im, gen))
    check(not hasattr(gen, "BELT_ZONE") and not hasattr(gen, "SWORD_ZONE"), "no belt or katana zones are left in the generator")
    check(all(z[1] >= max(gen.LABEL_LEFT[3], gen.LABEL_RIGHT[3]) + 30 for z in gen.EMPTY_ZONES), "the empty zones start below both label rows")
    # broken: a planted prop on either lower wall, or on the floor, is reported
    for z, why in ((gen.EMPTY_ZONES[0], "lower-left"), (gen.EMPTY_ZONES[1], "lower-right")):
        prop = im.copy()
        for k in range(12):
            prop.paste((226, 59, 59) if k % 2 else (255, 201, 77), (z[0] + 20 + k * 14, z[1] + 20, z[0] + 28 + k * 14, z[1] + 100))
        check(zone_problems(prop, gen.EMPTY_ZONES) != [], "BROKEN: a prop on the %s wall is reported" % why)
    dark = im.copy()
    dark.paste((8, 6, 26), (250, 850, 330, 1000))
    check("floor not clear" in floor_problems(dark, gen), "BROKEN: a prop standing on the floor is reported")


if __name__ == "__main__":
    main(globals())
